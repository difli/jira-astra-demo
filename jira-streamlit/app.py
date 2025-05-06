import streamlit as st
import pandas as pd
import json, time, requests

import pulsar
from astrapy import DataAPIClient

from openai import OpenAI
import instructor
from pydantic import BaseModel, Field

from datetime import datetime

# Import necessary modules
from agents.agent import Agent
from langchain_core.messages import HumanMessage
import uuid

def format_datetime(date_str):
    # Parse the datetime string and format it nicely
    dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%f%z")
    return dt.strftime("%B %d, %Y, %I:%M:%S %p %Z")  # e.g., "November 5, 2024, 12:28:32 AM UTC"

st.set_page_config(page_title="Jira Assistant: Real-Time Issue Tracking & Insights", page_icon="🛠️")
col1, col2 = st.columns([0.8, 0.2])
with col1:
    st.title("Jira Assistant: Real-Time Issue Tracking & Insights")
with col2:
    st.image("assets/Jira_Logo.svg.png", use_container_width=True)  # Updated parameter

tab1, tab2, tab3, tab4 = st.tabs(["Real-time updates", "Management", "Chat", "Langflow powered Chat"])


class Metadata(BaseModel):
    """Metadata of the content"""
    category: str = Field(..., description="Category of the content, e.g., Software Development, Product Management, Prject Management, ...")
    sentiment: str = Field(..., description="Sentiment score from 0 (negative) to 100 (positive); only numbers")


# Initialize the session state
if "stream" not in st.session_state:
    st.session_state.stream = []
if "langflow_endpoint" not in st.session_state:
    st.session_state.langflow_endpoint = ""
if "chat_thread" not in st.session_state:
    st.session_state.chat_thread = []


# Initialize Pulsar client and consumer
@st.cache_resource(show_spinner='Connecting to Pulsar')
def init_pulsar():
    client = pulsar.Client(
        st.secrets["PULSAR_SERVICE"],
        authentication=pulsar.AuthenticationToken(st.secrets["PULSAR_TOKEN"])
    )
    consumer = client.subscribe(
        st.secrets["PULSAR_TOPIC"], subscription_name='jira-updates'
    )
    return client, consumer

client, consumer = init_pulsar()


# Cache the Astra DB Vector Store and collection
@st.cache_resource(show_spinner='Connecting to Astra')
def load_vector_store_collection():
    client = DataAPIClient(st.secrets['ASTRA_DB_APPLICATION_TOKEN'])
    db = client.get_database_by_api_endpoint(st.secrets['ASTRA_DB_API_ENDPOINT'])
    collection = db.get_collection(st.secrets['ASTRA_COLLECTION_NAME'])
    return collection

collection = load_vector_store_collection()

# Initialize the agent at the start
@st.cache_resource(show_spinner="Setting up the agent...")
def initialize_agent():
    return Agent()

agent = initialize_agent()

# Show Jira update from Pulsar stream
def show_jira_update(stream, placeholder):
    data = stream[-10:][::-1]
    content = ""
    for item in data:
        # Format created and updated dates
        timestamp = format_datetime(item['timestamp'])

        # Format the comments with nicely formatted timestamps
        comments = "\n".join([
            f"- **{comment['author']}**: {comment['body']} ({format_datetime(comment['created'])})"
            for comment in item.get('comments', [])
        ])

        # Build the content string with formatted date and time
        content += (
            f"**[{item['key']}]({item['url']})**\\\n"
            f"**Summary**: {item['fields'].get('summary', 'No summary provided')}\n\n"
            f"🕑&nbsp;Updated: {timestamp}&nbsp;&nbsp;&nbsp;#️⃣&nbsp;Count: {item.get('count', 'N/A')}&nbsp;&nbsp;&nbsp;📢&nbsp;Event Type: {item.get('event_type', 'N/A')}\n\n")

    # Display the content
    placeholder.markdown(content)

# Stream Jira updates from Pulsar
def show_jira_updates(placeholder):
    try:
        while True:
            msg = consumer.receive(timeout_millis=1000)
            if msg:
                data = json.loads(msg.data())
                data["count"] = len(st.session_state.stream) + 1
                st.session_state.stream.append(data)
                consumer.acknowledge(msg)
                show_jira_update(st.session_state.stream, placeholder)
            else:
                break
    except pulsar.Timeout:
        pass


# Chat with the user based on Astra DB data
def show_chat_qa(question, date, answer_placeholder, sources_placeholder):
    filter = {"metadata.date": str(date)} if date else {}
    results = collection.find(
        filter,
        sort={
            "$vectorize": question
        },
        limit=20,
        include_similarity=True
    )

    # Construct the context
    context = ""
    sources = ""
    for result in results:
        formatted_date = format_datetime(result.get('metadata', {}).get('timestamp'))
        context += f"{result['content']}\n\n"
        sources += (
            f"**[{result['metadata'].get('key', 'No summary available')}]"
            f"({result['metadata'].get('url', '#')})**&nbsp;&nbsp;&nbsp;"
            f"📅&nbsp;&nbsp;{formatted_date}&nbsp;&nbsp;&nbsp;"
            f"📈&nbsp;{round(result.get('$similarity', 0) * 100, 1)}%\\\n"
        )

    # Now pass the context to the Chat Completion
    client = OpenAI(api_key=st.secrets['OPENAI_API_KEY'])
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system",
             "content": "You're an expert in jira issues and you specialize in summarizing information from jira issues."},
            {"role": "system",
             "content": "Only use the information provided in the context to answer the question. When there is no relevant information, just say so."},
            {"role": "system",
             "content": "When helpful, make use of summary to better understand the content. Also you can make use of numbered lists to better structure the information."},
            {"role": "user", "content": f"Context: {context}"},
            {"role": "user", "content": f"Question: {question}"}
        ],
        stream=True
    )

    # Streaming response
    streaming_content = ""
    for chunk in response:
        chunk_content = chunk.choices[0].delta.content
        if chunk_content is not None:
            streaming_content += chunk_content
            answer_placeholder.markdown(f"{streaming_content}▌")

    # Final update to the answer placeholder
    answer_placeholder.markdown(f"{streaming_content[:-1]}")
    sources_placeholder.markdown(f"#### Sources used\n{sources[:-2]}")


def concatenate_metadata(metadata):
    """
    Concatenate summary, description, and all comments from the metadata JSON structure.

    Args:
        metadata (dict): Metadata JSON structure.

    Returns:
        str: Concatenated string containing summary, description, and all comments.
    """
    # Extract fields safely
    summary = metadata.get("fields", {}).get("summary", "")
    description = metadata.get("fields", {}).get("description", "")
    comments = metadata.get("fields", {}).get("comment", {}).get("comments", [])

    # Initialize the text_parts list
    text_parts = []

    # Add summary if present
    if summary:
        text_parts.append(summary)

    # Add description if present
    if description:
        text_parts.append(description)

    # Add comments if present
    for comment in comments:
        comment_body = comment.get("body", "")
        if comment_body:
            text_parts.append(comment_body)

    # Concatenate all parts with a separator (e.g., double newline for readability)
    concatenated_text = "\n\n".join(text_parts)

    return concatenated_text


def enrich_metadata(content):
    client = instructor.from_openai(OpenAI(api_key=st.secrets['OPENAI_API_KEY']))
    results = client.chat.completions.create(
        model="gpt-4o",
        response_model=Metadata,
        messages=[
            {"role": "system", "content": "You are a Jira assistant specializing in categorizing Jira story content and determining the sentiment of the content. Provide accurate and concise metadata extraction."},
            {"role": "user", "content": f"Analyze the following content to extract metadata: {content}"},
            {"role": "user", "content": "From the provided content, extract the relevant metadata (e.g., category, sentiment) and format it as JSON."}
        ]
    )

    json_result = results.model_dump()
    return json_result

# Define the missing function `show_search_results`
def show_search_results(results, placeholder):
    with placeholder:
        for result in results:
            metadata = enrich_metadata(concatenate_metadata(result['metadata']))
            sentiment_emoji = "😊" if int(metadata['sentiment']) > 55 else "😞" if int(
                metadata['sentiment']) < 45 else "😐"
            st.markdown(f"""
            **<a href="{result['metadata']['url']}">{result['metadata']['key']}</a>**<br>
            🕑&nbsp;&nbsp;{format_datetime(result['metadata']['timestamp']) if result['metadata'].get('timestamp') else 'Not provided'}&nbsp;&nbsp;&nbsp;
            📈&nbsp;{round(result['$similarity'] * 100, 1)}%&nbsp;&nbsp;&nbsp;
            🗂️&nbsp;{metadata['category']}&nbsp;&nbsp;&nbsp;
            {sentiment_emoji}&nbsp;{metadata['sentiment']}<br><br>
            <i>{result['metadata']['fields']['summary'][0:400]}...</i><br><br>
            """, unsafe_allow_html=True)


with tab1:
    subscribed = st.checkbox("Subscribe to real-time updates", value=False)
    placeholder = st.empty()
    if subscribed:
        while True:
            show_jira_updates(placeholder)
            time.sleep(1)

with tab2:
    category = st.selectbox("Select a category", ["Software Development", "Product Management", "Project Management"])
    selected_date = st.date_input("Select an update date", value=pd.to_datetime("today"))
    story_count = st.number_input("Number of stories", value=5)
    update_button = st.button("Show")
    placeholder = st.container()

    if update_button:
        results = collection.find(
            {"metadata.date": str(selected_date)},
            sort={"$vectorize": f"Content related to {category.lower()}"},
            include_similarity=True,
            limit=story_count
        )
        show_search_results(results, placeholder)


with tab3:
    # Define the questions for each chat mode
    vector_search_questions = [
        "What's up with my Jira issues?",
        "What is the status of issue TES-10?",
        "When was issue TES-10 created?",
        "Can you summarize issue TES-10 for me?",
        "Who reported issue TES-10?",
        "Ask your own question..."
    ]

    agent_questions = [
        "List all issues in project TES?",
        "What is the status of issue TES-10?",
        "Can you summarize issue TES-10 for me?",
        "Which issues are similar to issue TES-10?",
        "What are the issues in project TES?",
        "What is the priority of issue TES-10?",
        "Who is the reporter of issue TES-10?",
        "What are the details of issue TES-10?",
        "List all issues with status 'To Do' in project TES.",
        "What are the labels for issue TES-10?",
        "Show all comments on issue TES-10.",
        "Which project does issue TES-10 belong to?",
        "What type of issue is TES-10?",
        "Can you provide the description of issue TES-10?",
        "Who created issue TES-10?",
        "What is the latest update date for issue TES-10?",
        "Find issues with similar summaries to issue TES-10.",
        "How many issues are labeled 'bug' in project TES?",
        "What is the color of the status category for issue TES-10?",
        "Ask your own question..."
    ]

    # Chat mode selection
    chat_mode = st.radio(
        "Choose your chat mode:",
        options=["Vector Search", "Agent (with Database Query tools)"],
        horizontal=True
    )

    # Display corresponding questions based on the selected chat mode
    if chat_mode == "Vector Search":
        selected_questions = vector_search_questions
    elif chat_mode == "Agent (with Database Query tools)":
        selected_questions = agent_questions

    # Question input
    question = st.selectbox("Select a question", options=selected_questions)
    custom_question = st.text_input("Ask Jira an issue question", disabled=question != "Ask your own question...")
    col1, col2, col3 = st.columns([0.3, 0.3, 0.4], vertical_alignment="bottom")
    date_toggle = col1.toggle("Filter by date", value=False)
    date = col2.date_input("Select a date", value=pd.to_datetime("today"), key="chat", disabled=not date_toggle)
    update_button = col3.button("Show me!", key="update_chat", use_container_width=True)
    answer_placeholder = st.empty()
    sources_placeholder = st.empty()

    # Final question based on selection or custom input
    question = custom_question if question == "Ask your own question..." else question

    if update_button:
        if chat_mode == "Vector Search":
            # Execute pure vector search-based chat
            show_chat_qa(question, date if date_toggle else None, answer_placeholder, sources_placeholder)
        elif chat_mode == "Agent (with Database Query tools)":
            # Execute agent-based chat
            try:
                thread_id = str(uuid.uuid4())
                messages = [HumanMessage(content=question)]
                config = {'configurable': {'thread_id': thread_id}}

                # Use the pre-initialized agent
                result = agent.graph.invoke({'messages': messages}, config=config)

                st.write("Response:", result['messages'][-1].content)
            except Exception as e:
                st.error(f"An error occurred: {e}")

with tab4:
    langflow_endpoint = st.text_input("Langflow REST endpoint", key="langflow_endpoint")
    if langflow_endpoint:
        messages = st.container()
        if query := st.text_input("Say something"):
            st.session_state.chat_thread.append({'role': 'user', 'content': query})
            messages.markdown(f"**User**: {query}")

            # Call Langflow REST API
            response = requests.post(
                langflow_endpoint,
                headers={"Authorization": f"Bearer {st.secrets['ASTRA_DB_APPLICATION_TOKEN']}"},
                json={"input_value": query, "output_type": "chat", "input_type": "chat"}
            )
            result = response.json()["outputs"][0]["outputs"][0]["outputs"]["message"]["message"]
            st.session_state.chat_thread.append({'role': 'assistant', 'content': result})
            messages.markdown(f"**Assistant**: {result}")

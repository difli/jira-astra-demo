import datetime
import operator
import os
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.tools.json_query import json_query
from agents.tools.find_similar_issues import find_similar_issues

import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
_ = load_dotenv()

CURRENT_YEAR = datetime.datetime.now().year

# Define Agent State
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]

# Define system prompts
TOOLS_SYSTEM_PROMPT = """You are an expert JIRA assistant. Use the most appropriate tool to process user queries.
            
            - If the query asks for **similar issues** or mentions "similar to issue," use the `find_similar_issues` tool.
            - If the query asks for general information about issues, projects, or JIRA metadata, use the `json_query` tool.

            Always extract necessary parameters from the query. For example:
            - If the user asks about "issues in project TES," extract `project_key=TES`.
            - If the user asks "find similar issues to TES-10," extract `issue_key=TES-10` for the `find_similar_issues` tool.

            Your goal is to provide accurate and concise information based on JIRA data."""

# Define tools
TOOLS = [json_query, find_similar_issues]

class Agent:
    def __init__(self):
        # Initialize tools
        self._tools = {t.name: t for t in TOOLS}
        self._tools_llm = ChatOpenAI(model='gpt-4o').bind_tools(TOOLS)

        # Initialize graph
        builder = StateGraph(AgentState)
        builder.add_node('call_tools_llm', self.call_tools_llm)
        builder.add_node('invoke_tools', self.invoke_tools)
        builder.add_node('answer_sender', self.answer_sender)
        builder.set_entry_point('call_tools_llm')

        # Add edges
        builder.add_conditional_edges(
            'call_tools_llm',
            Agent.exists_action,
            {'more_tools': 'invoke_tools', 'answer_sender': 'answer_sender'}
        )
        builder.add_edge('invoke_tools', 'call_tools_llm')
        builder.add_edge('answer_sender', END)

        # Set up memory and compile the graph
        memory = MemorySaver()
        self.graph = builder.compile(checkpointer=memory, interrupt_before=['answer_sender'])

        # Log the graph structure
        logger.info("Graph structure:\n%s", self.graph.get_graph().draw_mermaid())

    @staticmethod
    def exists_action(state: AgentState):
        """Determine next step based on whether there are pending tool calls."""
        result = state['messages'][-1]
        if len(result.tool_calls) == 0:
            return 'answer_sender'
        return 'more_tools'

    def answer_sender(self, state: AgentState):
        """Handle sending the final answer to the user."""
        logger.info("Preparing final answer.")
        final_message = state['messages'][-1].content
        logger.info("Final response: %s", final_message)

    def call_tools_llm(self, state: AgentState):
        """Call the LLM to decide tool usage."""
        messages = [SystemMessage(content=TOOLS_SYSTEM_PROMPT)] + state['messages']
        try:
            message = self._tools_llm.invoke(messages)
            return {'messages': [message]}
        except Exception as e:
            logger.error("Error calling tools LLM: %s", e)
            return {'messages': []}

    def invoke_tools(self, state: AgentState):
        """Invoke tools based on the tool calls."""
        tool_calls = state['messages'][-1].tool_calls
        results = []
        for t in tool_calls:
            try:
                logger.info("Calling tool: %s with args: %s", t['name'], t['args'])
                if t['name'] not in self._tools:
                    logger.warning("Invalid tool name: %s", t['name'])
                    result = 'Invalid tool name, retry'
                else:
                    result = self._tools[t['name']].invoke(t['args'])
                results.append(
                    ToolMessage(tool_call_id=t['id'], name=t['name'], content=str(result))
                )
            except Exception as e:
                logger.error("Error invoking tool %s: %s", t['name'], e)
                results.append(
                    ToolMessage(tool_call_id=t['id'], name=t['name'], content=f"Error: {e}")
                )
        logger.info("Tool invocation complete. Returning results to model.")
        return {'messages': results}

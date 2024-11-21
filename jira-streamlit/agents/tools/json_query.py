import os
from typing import Optional, Dict, List
from astrapy import DataAPIClient
from dotenv import load_dotenv
from langchain.pydantic_v1 import BaseModel, Field
from langchain_core.tools import tool

# Load environment variables
load_dotenv()

# Define input schema
class JiraQueryInput(BaseModel):
    issue_id: Optional[str] = Field(None, description="The numerical ID of the JIRA issue to retrieve.")
    issue_key: Optional[str] = Field(None, description="The key of the JIRA issue (e.g., TES-10).")
    project_key: Optional[str] = Field(None, description="The key of the JIRA project (e.g., TES).")
    summary_contains: Optional[str] = Field(None, description="Text that should be contained in the summary field.")
    status: Optional[str] = Field(None, description="Status of the JIRA issue (e.g., 'To Do').")
    limit: int = Field(10, description="Maximum number of results to return. If all is requested then limit is 0")

# Initialize the Astra DB collection
def initialize_astra_collection():
    """
    Connect to Astra DB and return the collection object.
    """
    token = os.getenv("ASTRA_DB_APPLICATION_TOKEN")
    endpoint = os.getenv("ASTRA_DB_API_ENDPOINT")
    collection_name = os.getenv("ASTRA_COLLECTION_NAME")

    if not all([token, endpoint, collection_name]):
        raise ValueError("Missing one or more required environment variables: "
                         "ASTRA_DB_APPLICATION_TOKEN, ASTRA_DB_API_ENDPOINT, ASTRA_COLLECTION_NAME")

    client = DataAPIClient(token)
    db = client.get_database_by_api_endpoint(endpoint)
    collection = db.get_collection(collection_name)
    return collection

class JiraQueryInputSchema(BaseModel):
    params: JiraQueryInput

@tool(args_schema=JiraQueryInputSchema)
def json_query(params: JiraQueryInput):
    """
    Query JIRA issue data from Astra DB.

    Args:
        params (JiraQueryInput): Parameters for querying the JIRA database.

    Returns:
        List[Dict]: Matching issues with details.
    """
    try:
        print(f"Received params: {params}")  # Debugging log for received arguments
        collection = initialize_astra_collection()

        # Build the filter and sorting condition
        filter_conditions = {}
        sort_condition = None

        if params.issue_key:
            filter_conditions["metadata.key"] = params.issue_key
        elif params.issue_id:
            filter_conditions["metadata.id"] = params.issue_id
        if params.project_key:
            filter_conditions["metadata.fields.project.key"] = params.project_key
        if params.status:
            filter_conditions["metadata.fields.status.name"] = params.status
        if params.summary_contains:
            sort_condition = {"$vectorize": params.summary_contains}

        # Log the constructed query
        print(f"Constructed Astra DB Query:\n"
              f"Filter: {filter_conditions}\n"
              f"Sort: {sort_condition}\n"
              f"Limit: {params.limit}")

        # Determine query arguments
        query_args = {
            "filter": filter_conditions,
            "limit": params.limit,
        }
        if sort_condition:
            query_args["sort"] = sort_condition
            query_args["include_similarity"] = True
            print("Adding include_similarity=True for vector search")

        # Query the collection
        results = collection.find(**query_args)

        # Format results
        formatted_results = []
        for result in results:
            fields = result.get("metadata", {}).get("fields", {})
            formatted_results.append({
                "id": result.get("metadata", {}).get("id"),
                "key": result.get("metadata", {}).get("key"),
                "summary": fields.get("summary"),
                "description": fields.get("description"),
                "status": fields.get("status", {}).get("name"),
                "status_description": fields.get("status", {}).get("description"),
                "status_category": fields.get("status", {}).get("statusCategory", {}).get("name"),
                "status_category_color": fields.get("status", {}).get("statusCategory", {}).get("colorName"),
                "priority": fields.get("priority", {}).get("name"),
                "issuetype": fields.get("issuetype", {}).get("name"),
                "issuetype_description": fields.get("issuetype", {}).get("description"),
                "project_key": fields.get("project", {}).get("key"),
                "project_name": fields.get("project", {}).get("name"),
                "project_type": fields.get("project", {}).get("projectTypeKey"),
                "created": fields.get("created"),
                "updated": fields.get("updated"),
                "creator_email": fields.get("creator", {}).get("emailAddress"),
                "creator_displayName": fields.get("creator", {}).get("displayName"),
                "reporter_email": fields.get("reporter", {}).get("emailAddress"),
                "reporter_displayName": fields.get("reporter", {}).get("displayName"),
                "labels": fields.get("labels", []),
                "comments": [
                    {
                        "author_displayName": comment.get("author", {}).get("displayName"),
                        "author_email": comment.get("author", {}).get("emailAddress"),
                        "body": comment.get("body"),
                        "created": comment.get("created"),
                        "updated": comment.get("updated"),
                    }
                    for comment in fields.get("comment", {}).get("comments", [])
                ],
                "similarity": result.get("$similarity", 0),
            })

        print(f"Query Results: {formatted_results}")  # Debug log for results
        return {"results": formatted_results}

    except Exception as e:
        print(f"Error during query execution: {e}")  # Log errors for debugging
        return {"error": str(e)}

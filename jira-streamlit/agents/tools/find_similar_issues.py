from astrapy import DataAPIClient
from langchain.pydantic_v1 import BaseModel, Field
from langchain_core.tools import tool
import os

# Initialize Astra DB Collection
def initialize_astra_collection():
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

# Input Schema for Finding Similar Issues
class SimilarIssuesInput(BaseModel):
    issue_key: str = Field(..., description="The key of the issue to find similar issues for.")
    limit: int = Field(10, description="The maximum number of similar issues to return.")

# Decorate the tool
@tool("find_similar_issues", args_schema=SimilarIssuesInput)
def find_similar_issues(issue_key: str, limit: int = 10):
    """
    Find issues similar to a given issue key using Astra DB.
    """
    try:
        collection = initialize_astra_collection()

        # Retrieve the issue's summary
        issue_filter = {"metadata.key": issue_key}
        issue_result = list(collection.find(filter=issue_filter, limit=1))

        if not issue_result:
            return {"error": f"No issue found with key {issue_key}"}

        issue_summary = issue_result[0].get("metadata", {}).get("fields", {}).get("summary")
        if not issue_summary:
            return {"error": f"No summary available for issue {issue_key}"}

        # Log the retrieved summary
        print(f"Retrieved Summary for {issue_key}: {issue_summary}")

        # Use summary for vectorized similarity query
        sort_condition = {"$vectorize": issue_summary}
        similar_issues = list(collection.find(
            sort=sort_condition,
            limit=limit,
            include_similarity=True
        ))

        if not similar_issues:
            return {"message": f"No similar issues found for {issue_key}"}

        # Format results
        formatted_results = []
        for result in similar_issues:
            formatted_results.append({
                "id": result.get("metadata", {}).get("id"),
                "key": result.get("metadata", {}).get("key"),
                "summary": result.get("metadata", {}).get("fields", {}).get("summary"),
                "similarity": result.get("$similarity", 0),
            })

        return {"similar_issues": formatted_results}

    except Exception as e:
        return {"error": str(e)}

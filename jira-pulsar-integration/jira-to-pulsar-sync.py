import os
import json
import requests
import pulsar
from dotenv import load_dotenv
from datetime import datetime, timezone

# Load environment variables from .env file
load_dotenv()


def get_pulsar_client():
    """Initialize and return the Pulsar client and jira-pulsar-integration."""
    pulsar_service_url = os.getenv("PULSAR_SERVICE")
    pulsar_token = os.getenv("PULSAR_TOKEN")
    pulsar_topic = os.getenv("PULSAR_TOPIC")
    client = pulsar.Client(pulsar_service_url, authentication=pulsar.AuthenticationToken(pulsar_token))
    producer = client.create_producer(pulsar_topic)
    return client, producer


def get_jira_config():
    """Retrieve Jira configuration from environment variables."""
    return {
        "jira_url": os.getenv("JIRA_URL_LOCAL"),
        "jira_username": os.getenv("JIRA_USERNAME_LOCAL"),
        "jira_password": os.getenv("JIRA_PASSWORD_LOCAL")
    }


# Setup Pulsar client and jira-pulsar-integration
client, producer = get_pulsar_client()
jira_config = get_jira_config()


def get_all_issues():
    """Fetch all issues from Jira with pagination."""
    issues = []
    start_at = 0
    max_results = 50  # Adjust the number of issues per request if necessary

    while True:
        url = f"{jira_config['jira_url']}/rest/api/2/search"
        auth = (jira_config["jira_username"], jira_config["jira_password"])
        headers = {"Content-Type": "application/json"}
        params = {
            "jql": "ORDER BY created DESC",  # Adjust JQL as needed
            "startAt": start_at,
            "maxResults": max_results,
        }

        try:
            response = requests.get(url, headers=headers, auth=auth, params=params)
            response.raise_for_status()
            data = response.json()

            issues.extend(data.get("issues", []))
            start_at += max_results

            if len(data["issues"]) < max_results:
                break

        except requests.RequestException as e:
            print(f"Error fetching issues: {e}")
            break

    return issues


def get_issue_details(issue_id):
    """Fetch detailed information about an issue from Jira."""
    url = f"{jira_config['jira_url']}/rest/api/2/issue/{issue_id}"
    auth = (jira_config["jira_username"], jira_config["jira_password"])
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, auth=auth)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching details for issue {issue_id}: {e}")
        return None


def filter_relevant_fields(issue_data):
    """Filter out irrelevant fields while keeping the original structure, including comments."""
    fields = issue_data.get("fields", {})

    # Define relevant fields and subfields
    relevant_fields = {"id", "key", "fields"}
    relevant_subfields = {
        "issuetype",
        "project",
        "priority",
        "summary",
        "description",
        "status",
        "creator",
        "reporter",
        "created",
        "updated",
        "labels",
        "comment"
    }
    relevant_nested_fields = {
        "statusCategory": {"key", "name", "colorName"},
        "creator": {"displayName", "emailAddress", "timeZone"},
        "reporter": {"displayName", "emailAddress", "timeZone"},
        "project": {"key", "name", "projectTypeKey"},
        "issuetype": {"name", "description", "subtask"}
    }

    # Retain only relevant top-level fields
    filtered_data = {k: v for k, v in issue_data.items() if k in relevant_fields}

    # Filter subfields inside "fields"
    if "fields" in filtered_data:
        filtered_fields = {}
        for field_key, field_value in filtered_data["fields"].items():
            if field_key in relevant_subfields:
                if isinstance(field_value, dict) and field_key in relevant_nested_fields:
                    # Filter nested fields within a subfield
                    filtered_fields[field_key] = {
                        k: v for k, v in field_value.items() if k in relevant_nested_fields[field_key]
                    }
                elif field_key == "comment" and isinstance(field_value, dict):
                    # Special handling for comments
                    filtered_fields[field_key] = {
                        "comments": [
                            {
                                "author": {
                                    "displayName": comment.get("author", {}).get("displayName"),
                                    "emailAddress": comment.get("author", {}).get("emailAddress")
                                },
                                "body": comment.get("body"),
                                "created": comment.get("created"),
                                "updated": comment.get("updated")
                            }
                            for comment in field_value.get("comments", [])
                        ]
                    }
                else:
                    # Retain the field as is if no special handling is needed
                    filtered_fields[field_key] = field_value

        filtered_data["fields"] = filtered_fields

    return filtered_data


def prepare_data_for_pulsar(issue_data, event_type="initial_load"):
    """Prepare data payload for Pulsar with essential fields and comments."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+0000"
    filtered_data = filter_relevant_fields(issue_data)
    filtered_data["timestamp"] = timestamp
    filtered_data["date"] = timestamp.split('T')[0]
    filtered_data["event_type"] = event_type
    filtered_data["url"] = f"{jira_config['jira_url']}/browse/" + issue_data.get("key", "")
    return filtered_data


def send_to_pulsar(data):
    """Send prepared data to Pulsar."""
    try:
        producer.send(json.dumps(data).encode('utf-8'))
        print(f"Data sent to Pulsar successfully: {data['id']}")
    except Exception as e:
        print(f"Error sending data to Pulsar: {e}")


def main():
    """Main function to load all issues and send to Pulsar."""
    issues = get_all_issues()
    if not issues:
        print("Failed to retrieve issues from Jira.")
        return

    for issue in issues:
        issue_data = get_issue_details(issue["id"])
        if issue_data:
            data_for_pulsar = prepare_data_for_pulsar(issue_data)
            send_to_pulsar(data_for_pulsar)


# Run the script
if __name__ == "__main__":
    try:
        main()
    finally:
        producer.close()
        client.close()

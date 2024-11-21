from pulsar import Function
import json


class VectorizeFunction(Function):
    def __init__(self):
        pass

    def process(self, input, context):
        try:
            # Parse the input JSON payload
            payload = json.loads(input)

            # Create the result payload
            result = {}

            # Add '_id' field with issue_id if present in payload
            if "id" in payload:
                result["_id"] = payload["id"]

            # Assign the payload JSON as a string to '$vectorize' and 'content'
            result["$vectorize"] = json.dumps(payload)  # Full JSON as string
            result["content"] = json.dumps(payload)    # Same stringified JSON for 'content'

            # Add 'metadata' field to main payload for additional information
            result["metadata"] = payload

            # Return the result as a JSON string
            return json.dumps(result)

        except KeyError as e:
            context.get_logger().error(f"Key error: {e}")
        except Exception as e:
            context.get_logger().error(f"An error occurred: {e}")

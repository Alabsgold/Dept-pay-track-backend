from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        data = response.data

        # If data is already in {"error": "...", "message": "..."} shape, keep it
        if isinstance(data, dict) and "error" in data and "message" in data:
            return response

        # If detail exists
        if isinstance(data, dict) and "detail" in data:
            detail_msg = str(data["detail"])
            if response.status_code == 401:
                machine_code = "unauthorized"
            elif response.status_code == 403:
                machine_code = "permission_denied"
            elif response.status_code == 404:
                machine_code = "not_found"
            elif response.status_code == 409:
                machine_code = "conflict"
            else:
                code = getattr(data["detail"], "code", None)
                machine_code = str(code) if code else "error"

            response.data = {
                "error": machine_code,
                "message": detail_msg,
            }
            return response

        # If validation error dictionary e.g. {"field": ["msg"]}
        if isinstance(data, dict):
            messages = []
            for field, errors in data.items():
                if isinstance(errors, list):
                    err_str = " ".join(str(e) for e in errors)
                else:
                    err_str = str(errors)
                # Non-field errors stay field-anonymous (QA M-6): the §8
                # generic duplicate-registration message must not reveal —
                # not even via a prefix — which identifier collided.
                if field == 'non_field_errors':
                    messages.append(err_str)
                else:
                    messages.append(f"{field}: {err_str}")
            combined_message = "; ".join(messages)
            response.data = {
                "error": "bad_request",
                "message": combined_message,
            }
            return response

        if isinstance(data, list):
            combined_message = "; ".join(str(e) for e in data)
            response.data = {
                "error": "bad_request",
                "message": combined_message,
            }
            return response

    return response

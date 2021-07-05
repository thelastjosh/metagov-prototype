
temporary_plugin_config_schema = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "user": {
            "type": "string",
            "description": "Actions will be taken on behalf of this user."
        }
        "personal_access_token": {
            "type": "string",
            "description": "Personal access token for user"
        }
    },
    "required": ["user", "personal_access_token"],
}

plugin_config_schema = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "api_key": {
            "type": "string",
            "description": "Github API key for a bot user that is an admin. Actions will be taken on behalf of this user."
        }
    },
    "required": ["api_key"],
}

# Webhook Event Schemas

# expense_created_event = {
#     "type": "object",
#     "additionalProperties": True,
#     "properties": {
#         "url": {"type": "string"}
#     }
# }


# Action Schemas

get_issues_parameters = {
    "type": "object",
    "properties": {
        "owner_name": {"type": "string"},
        "repo_name": {"type": "string"}
    },
    "required": ["owner_name", "repo_name"],
}

create_issue_parameters = {
    "type": "object",
    "properties": {
        "owner_name": {"type": "string"},
        "repo_name": {"type": "string"},
        "title": {"type": "string"},
        "body": {"type": "string"}
    },
    "required": ["owner_name", "repo_name", "title", "body"],
}

# Governance Process Schemas

issue_react_vote_parameters = {
    "type": "object",
    "properties": {
        # Required
        "owner_name": {"type": "string"},
        "repo_name": {"type": "string"},
        "question": {"type": "string"},
        # Optional
        "help_text": {"type": "string"},
        "max_votes": {"type": "integer"},
        "track_progress": {"type": "string"}
    },
    "required": ["owner_name", "repo_name", "question"],
}

issue_comment_vote_parameters = {
    "type": "object",
    "properties": {
        # Required
        "owner_name": {"type": "string"},
        "repo_name": {"type": "string"},
        "question": {"type": "string"},
        # Optional
        "help_text": {"type": "string"},
        "max_votes": {"type": "integer"},
        "track_progress": {"type": "string"}
    },
    "required": ["owner_name", "repo_name", "question"],
}
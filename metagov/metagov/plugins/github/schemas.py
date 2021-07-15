github_app_config_schema = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "owner": {
            "type": "string",
            "description": "Name of user or organization that has installed the app."
        },
        "installation_id": {
            "type": "string",
            "description": "Installation_ID can be gotten from github after manually installing app."
        }
    },
    "required": ["installation_id"],
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
        "repo_name": {"type": "string"}
    },
    "required": ["repo_name"],
}

create_issue_parameters = {
    "type": "object",
    "properties": {
        "repo_name": {"type": "string"},
        "title": {"type": "string"},
        "body": {"type": "string"}
    },
    "required": ["repo_name", "title", "body"],
}

# Governance Process Schemas

issue_react_vote_parameters = {
    "type": "object",
    "properties": {
        # Required
        "repo_name": {"type": "string"},
        "question": {"type": "string"},
        # Optional
        "help_text": {"type": "string"},
        "max_votes": {"type": "integer"},
        "track_progress": {"type": "string"}
    },
    "required": ["repo_name", "question"],
}

issue_comment_vote_parameters = {
    "type": "object",
    "properties": {
        # Required
        "repo_name": {"type": "string"},
        "question": {"type": "string"},
        # Optional
        "help_text": {"type": "string"},
        "max_votes": {"type": "integer"},
        "track_progress": {"type": "string"}
    },
    "required": ["repo_name", "question"],
}
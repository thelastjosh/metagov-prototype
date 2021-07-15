from drf_yasg import openapi
from metagov.core.middleware import COMMUNITY_HEADER


class Tags(object):
    GOVERNANCE_PROCESS = "Governance Processes"
    PUBLIC_ACTION = "Actions (Public)"
    ACTION = "Actions"
    COMMUNITY = "Community Configuration"
    PLUGIN_AUTH = "Plugin Auth"


community_header = openapi.Parameter(
    COMMUNITY_HEADER, openapi.IN_HEADER, required=True, type=openapi.TYPE_STRING, description="Unique community slug"
)

community_slug_in_path = openapi.Parameter(
    "slug", openapi.IN_PATH, required=True, type=openapi.TYPE_STRING, description="Unique community slug"
)
plugin_name_in_path = openapi.Parameter(
    "plugin_name", openapi.IN_PATH, required=True, type=openapi.TYPE_STRING, description="Plugin name"
)

plugins_list = openapi.Schema(
    type=openapi.TYPE_ARRAY,
    description="List of enabled plugins and their configs",
    items=openapi.Items(
        type=openapi.TYPE_OBJECT,
        properties={
            "name": openapi.Schema(type=openapi.TYPE_STRING, description="plugin name"),
            "config": openapi.Schema(type=openapi.TYPE_OBJECT, description="plugin config"),
        },
    ),
)

create_community_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "readable_name": openapi.Schema(type=openapi.TYPE_STRING, description="Human-readable community name"),
        "plugins": plugins_list,
    },
)

plugin_schemas = {
    "method": "get",
    "operation_id": "Get plugin configuration schemas in jsonschema format",
    "tags": [Tags.COMMUNITY],
}

plugin_metadata = {
    "method": "get",
    "operation_id": "Get plugin metadata and schemas",
    "responses": {
        200: openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "name": openapi.TYPE_STRING,
                "auth_type": openapi.TYPE_STRING,
                "uses_webhook": openapi.TYPE_BOOLEAN,
                "schemas": openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "config": openapi.Schema(type=openapi.TYPE_OBJECT, description="jsonschema for plugin config"),
                        "actions": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Items(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    "id": openapi.Schema(type=openapi.TYPE_STRING),
                                    "description": openapi.Schema(type=openapi.TYPE_STRING),
                                    "parameters_schema": openapi.Schema(type=openapi.TYPE_OBJECT),
                                    "response_schema": openapi.Schema(type=openapi.TYPE_OBJECT),
                                },
                            ),
                        ),
                        "processes": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Items(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    "id": openapi.Schema(type=openapi.TYPE_STRING),
                                    "description": openapi.Schema(type=openapi.TYPE_STRING),
                                    "parameters_schema": openapi.Schema(type=openapi.TYPE_OBJECT),
                                    "response_schema": openapi.Schema(type=openapi.TYPE_OBJECT),
                                },
                            ),
                        ),
                        "events": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Items(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    "type": openapi.Schema(type=openapi.TYPE_STRING),
                                    "source": openapi.Schema(type=openapi.TYPE_STRING),
                                    "schema": openapi.Schema(type=openapi.TYPE_OBJECT),
                                },
                            ),
                        ),
                    },
                ),
            },
        ),
    },
    "manual_parameters": [plugin_name_in_path],
    "tags": [Tags.COMMUNITY],
}

plugin_authorize = {
    "method": "get",
    "operation_id": "Authorize plugin",
    "operation_description": "Initiate an authorization flow to get user consent to install Metagov to some external platform, as defined in the plugin. On success, the plugin is enabled for the specified community, and this method returns a redirect to the specified redirect_uri.",
    "tags": [Tags.PLUGIN_AUTH],
    "manual_parameters": [
        openapi.Parameter(
            "plugin_name",
            openapi.IN_PATH,
            required=True,
            type=openapi.TYPE_STRING,
            description="The plugin to authorize",
        ),
        openapi.Parameter(
            "community",
            openapi.IN_QUERY,
            required=True,
            type=openapi.TYPE_STRING,
            description="Unique slug for an existing community",
        ),
        openapi.Parameter(
            "redirect_uri",
            openapi.IN_QUERY,
            required=True,
            type=openapi.TYPE_STRING,
            description="Where to redirect to after the oauth flow has completed",
        ),
    ],
}

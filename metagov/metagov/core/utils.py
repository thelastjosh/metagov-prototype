import json
import logging
import random
import jsonschema
from drf_yasg import openapi
from jsonschema_to_openapi.convert import convert
from django.conf import settings

logger = logging.getLogger(__name__)

internal_path = "api/internal"
WEBHOOK_SLUG_CONFIG_KEY = "webhook_slug"


def construct_webhook_url(plugin_instance):
    from metagov.core.plugin_decorators import plugin_registry

    if not plugin_uses_webhooks(plugin_registry[plugin_instance.name]):
        return None

    base = f"{settings.SERVER_URL}/api/hooks/{plugin_instance.community.slug}/{plugin_instance.name}"
    extra_slug = plugin_instance.config.get(WEBHOOK_SLUG_CONFIG_KEY)
    return f"{base}/{extra_slug}" if extra_slug else base


def plugin_uses_webhooks(cls):
    return cls._webhook_receiver_function is not None


def construct_action_url(plugin_name: str, slug: str, is_public=False) -> str:
    if is_public:
        return f"api/action/{plugin_name}.{slug}"
    return f"{internal_path}/action/{plugin_name}.{slug}"


def construct_process_url(plugin_name: str, slug: str) -> str:
    return f"{internal_path}/process/{plugin_name}.{slug}"


class SaferDraft7Validator(jsonschema.Draft7Validator):
    META_SCHEMA = {**jsonschema.Draft7Validator.META_SCHEMA, "additionalProperties": False}


def json_schema_to_openapi_object(json_schema):
    schema = convert(json_schema)
    return openapi.Schema(
        type=openapi.TYPE_OBJECT, properties=schema.get("properties", {}), required=schema.get("required", [])
    )


def generate_nonce(length=8):
    """Generate pseudorandom number."""
    return "".join([str(random.randint(0, 9)) for i in range(length)])


def restruct(d):
    for k in d:
        # convert value if it's valid json
        if isinstance(d[k], list):
            v = d[k]
            try:
                d[k] = json.loads(v[0])
            except ValueError:
                d[k] = v[0]

        # step into dictionary objects to convert string digits to integer
        if isinstance(d[k], dict):
            restruct(d[k])
        else:
            try:
                d[k] = int(d[k])
            except ValueError:
                d[k] = d[k]


def get_action_schemas(cls):
    actions = []
    for (name, meta) in cls._action_registry.items():
        actions.append(
            {
                "id": f"{cls.name}.{name}",
                "description": meta.description,
                "parameters_schema": meta.input_schema,
                "response_schema": meta.output_schema,
            }
        )
    return actions


def get_event_schemas(cls):
    result = []
    for event in cls._event_schemas:
        if event.get("type"):
            result.append({"event_type": event["type"], "source": cls.name, "schema": event.get("schema")})

    return result


def get_process_schemas(cls):
    processes = []
    for (name, process_cls) in cls._process_registry.items():
        processes.append(
            {
                "id": f"{cls.name}.{name}",
                "description": process_cls.description,
                "parameters_schema": process_cls.input_schema,
                "response_schema": process_cls.outcome_schema,
            }
        )
    return processes


def create_or_update_plugin(plugin_name, plugin_config, community):
    from metagov.core.plugin_decorators import plugin_registry
    from metagov.core.validators import DefaultValidatingDraft7Validator
    from rest_framework.exceptions import ValidationError

    cls = plugin_registry.get(plugin_name)
    if not cls:
        raise ValidationError(f"No such plugin registered: {plugin_name}")

    if cls.config_schema:
        try:
            # this mutates `plugin_config` by filling in default values from schema
            DefaultValidatingDraft7Validator(cls.config_schema).validate(plugin_config)
        except jsonschema.exceptions.ValidationError as err:
            raise ValidationError(f"{plugin_name} config validation error: {err.message}")

    try:
        plugin = cls.objects.get(name=plugin_name, community=community)
    except cls.DoesNotExist:
        inst = cls.objects.create(name=plugin_name, community=community, config=plugin_config)
        logger.info(f"Created plugin '{inst}'")
        return (inst, True)
    else:
        if plugin.config != plugin_config:
            # TODO what happens to pending processes?
            logger.info(f"Destroying and re-creating '{plugin}' to apply config change")
            plugin.delete()
            return (cls.objects.create(name=plugin_name, community=community, config=plugin_config), True)

        logger.info(f"Not updating '{plugin}', no change in config.")
        return (plugin, False)


# def jsonschema_to_parameters(schema):
#     #arg_dict["manual_parameters"].extend(jsonschema_to_parameters(meta.input_schema
#     schema = convert(schema)
#     properties = schema.get("properties", {})
#     required = schema.get("required", [])
#     parameters = []
#     for (name, prop) in properties.items():
#         param = openapi.Parameter(
#             name=name,
#             in_="query",
#             description=prop.get("description"),
#             type=prop.get("type"),
#             required=name in required,
#         )
#         parameters.append(param)
#     return parameters

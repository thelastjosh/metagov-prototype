import base64
import importlib
import json
import logging
from http import HTTPStatus

import jsonschema
import metagov.core.openapi_schemas as MetagovSchemas
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import decorator_from_middleware
from django.views.decorators.csrf import csrf_exempt
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from metagov.core import utils
from metagov.core.errors import PluginAuthError
from metagov.core.middleware import CommunityMiddleware
from metagov.core.models import Community, Plugin, ProcessStatus
from metagov.core.openapi_schemas import Tags
from metagov.core.plugin_constants import AuthType
from metagov.core.plugin_decorators import plugin_registry
from metagov.core.serializers import CommunitySerializer, GovernanceProcessSerializer, PluginSerializer
from requests.models import PreparedRequest
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import JSONParser

community_middleware = decorator_from_middleware(CommunityMiddleware)

logger = logging.getLogger(__name__)


def index(request):
    return redirect("/redoc")


@swagger_auto_schema(
    method="post",
    operation_id="Create community",
    operation_description="Create a new community",
    request_body=MetagovSchemas.create_community_schema,
    responses={200: CommunitySerializer, 201: CommunitySerializer},
    tags=[Tags.COMMUNITY],
)
@api_view(["POST"])
def create_community(request):
    data = JSONParser().parse(request)
    community_serializer = CommunitySerializer(data=data)
    if not community_serializer.is_valid():
        return JsonResponse(community_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    community_serializer.save()
    return JsonResponse(community_serializer.data, status=status.HTTP_201_CREATED)


@swagger_auto_schema(
    method="delete",
    operation_id="Delete community",
    manual_parameters=[MetagovSchemas.community_slug_in_path],
    operation_description="Delete an existing community",
    tags=[Tags.COMMUNITY],
)
@swagger_auto_schema(
    method="get",
    operation_id="Get community",
    operation_description="Get the configuration for an existing community",
    manual_parameters=[MetagovSchemas.community_slug_in_path],
    responses={200: CommunitySerializer},
    tags=[Tags.COMMUNITY],
)
@swagger_auto_schema(
    method="put",
    operation_id="Update community",
    operation_description="Update the configuration for an existing community",
    manual_parameters=[MetagovSchemas.community_slug_in_path],
    request_body=CommunitySerializer,
    responses={200: CommunitySerializer, 201: CommunitySerializer},
    tags=[Tags.COMMUNITY],
)
@api_view(["GET", "PUT", "DELETE"])
def community(request, slug):
    try:
        community = Community.objects.get(slug=slug)
    except Community.DoesNotExist:
        return HttpResponseNotFound()

    if request.method == "GET":
        # get community
        community_serializer = CommunitySerializer(community)
        return JsonResponse(community_serializer.data, safe=False)

    elif request.method == "PUT":
        # update community (change readable name or enable/disable plugins)
        data = JSONParser().parse(request)
        community_serializer = CommunitySerializer(community, data=data)
        if not community_serializer.is_valid():
            return JsonResponse(community_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        community_serializer.save()
        return JsonResponse(community_serializer.data)

    elif request.method == "DELETE":
        community.delete()
        return JsonResponse({"message": "Community was deleted successfully"}, status=status.HTTP_204_NO_CONTENT)


def decorated_enable_plugin_view(plugin_name):
    """
    Decorate the `enable_plugin` view with swagger schema properties defined by the plugin author
    """
    cls = plugin_registry[plugin_name]

    @community_middleware
    @api_view(["POST"])
    def enable_plugin(request):
        plugin_config = JSONParser().parse(request)
        # Create or re-create the plugin (only one instance per community supported for now!)
        plugin, created = utils.create_or_update_plugin(plugin_name, plugin_config, request.community)
        # Serialize and return the Plugin instance
        serializer = PluginSerializer(plugin)
        resp_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return JsonResponse(serializer.data, status=resp_status)

    request_body_schema = utils.json_schema_to_openapi_object(cls.config_schema) if cls.config_schema else {}

    return swagger_auto_schema(
        method="post",
        responses={
            201: openapi.Response(
                "Plugin enabled",
                PluginSerializer,
            ),
            200: openapi.Response(
                "The Plugin was already enabled. Plugin was updated if the config changed.",
                PluginSerializer,
            ),
        },
        operation_id=f"Enable {plugin_name}",
        tags=[Tags.COMMUNITY],
        operation_description=f"Enable {plugin_name} plugin.",
        manual_parameters=[MetagovSchemas.community_header],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                **request_body_schema.get("properties", {}),
            },
            required=request_body_schema.get("required", []),
        ),
    )(enable_plugin)


@swagger_auto_schema(
    method="delete",
    operation_id="Disable plugin",
    operation_description="Delete a plugin instance. This is an irreversible action.",
    manual_parameters=[MetagovSchemas.plugin_name_in_path],
    responses={204: "Plugin disabled successfully"},
    tags=[Tags.COMMUNITY],
)
@api_view(["DELETE"])
def delete_plugin(request, plugin_name, id):
    try:
        plugin = Plugin.objects.get(pk=id)
    except Plugin.DoesNotExist:
        return HttpResponseNotFound()
    plugin.delete()
    return HttpResponse(status=status.HTTP_204_NO_CONTENT)


@swagger_auto_schema(**MetagovSchemas.plugin_authorize)
@api_view(["GET"])
def plugin_authorize(request, plugin_name):
    plugin_cls = plugin_registry.get(plugin_name)
    if not plugin_cls:
        return HttpResponseBadRequest(f"No such plugin: {plugin_name}")

    # auth type (user login or app installation)
    type = request.GET.get("type")
    # required for "install" type. community to install to.
    community = request.GET.get("community")
    # where to redirect after auth flow is done
    redirect_uri = request.GET.get("redirect_uri")
    # state to pass along to final redirect after auth flow is done
    received_state = request.GET.get("state")
    request.session["received_authorize_state"] = received_state

    if type != AuthType.APP_INSTALL and type != AuthType.USER_LOGIN:
        return HttpResponseBadRequest(f"Parameter 'type' must be '{AuthType.APP_INSTALL}' or '{AuthType.USER_LOGIN}'")

    if type == AuthType.APP_INSTALL and not community:
        return HttpResponseBadRequest("Missing 'community'")

    if community is not None:
        # validate that community exists
        try:
            Community.objects.get(slug=community)
        except Community.DoesNotExist:
            return HttpResponseBadRequest(f"No such community: {community}")

    # Create the state
    nonce = utils.generate_nonce()
    state = {nonce: {"community": community, "redirect_uri": redirect_uri, "type": type}}
    state_str = json.dumps(state).encode("ascii")
    state_encoded = base64.b64encode(state_str).decode("ascii")
    # Store nonce in the session so we can validate the callback request
    request.session["nonce"] = nonce

    # FIXME: figure out a better way to register these functions
    plugin_views = importlib.import_module(f"metagov.plugins.{plugin_name}.views")

    url = plugin_views.get_authorize_url(state_encoded, type)

    if type == AuthType.APP_INSTALL:
        logger.info(f"Redirecting to authorize '{plugin_name}' for community {community}")
    elif type == AuthType.USER_LOGIN:
        logger.info(f"Redirecting to authorize user for '{plugin_name}'")
    return HttpResponseRedirect(url)


def redirect_with_params(url, params):
    req = PreparedRequest()
    req.prepare_url(url, params)
    return HttpResponseRedirect(req.url)


@swagger_auto_schema(method="GET", auto_schema=None)
@api_view(["GET"])
def plugin_auth_callback(request, plugin_name):
    logger.debug(f"Plugin auth callback received request: {request.GET}")
    plugin_cls = plugin_registry.get(plugin_name)
    if not plugin_cls:
        return HttpResponseBadRequest(f"No such plugin: {plugin_name}")
    state_str = request.GET.get("state")
    if not state_str:
        return HttpResponseBadRequest("missing state")

    # Validate and decode state
    nonce = request.session.get("nonce")
    if not nonce:
        return HttpResponseBadRequest("missing session nonce")

    state_obj = json.loads(base64.b64decode(state_str).decode("ascii"))
    logger.debug(f"Decoded state: {state_obj}")
    state = state_obj.get(nonce)
    type = state.get("type")
    community_slug = state.get("community")
    redirect_uri = state.get("redirect_uri")
    state_to_pass = request.session.get("received_authorize_state")

    if not redirect_uri:
        return HttpResponseBadRequest("bad state: redirect_uri is missing")

    if request.GET.get("error"):
        return redirect_with_params(redirect_uri, {"state": state_to_pass, "error": request.GET.get("error")})

    code = request.GET.get("code")
    if not code:
        return redirect_with_params(redirect_uri, {"state": state_to_pass, "error": "server_error"})

    community = None
    if type == AuthType.APP_INSTALL:
        # For installs, validate the community
        if not community_slug:
            return redirect_with_params(redirect_uri, {"state": state_to_pass, "error": "bad_state"})
        try:
            community = Community.objects.get(slug=community_slug)
        except Community.DoesNotExist:
            return redirect_with_params(redirect_uri, {"state": state_to_pass, "error": "community_not_found"})

    # FIXME: figure out a better way to register these functions
    plugin_views = importlib.import_module(f"metagov.plugins.{plugin_name}.views")

    try:
        return plugin_views.auth_callback(
            type=type,
            code=code,
            redirect_uri=redirect_uri,
            community=community,
            state=state_to_pass,
        )
    except PluginAuthError as e:
        return redirect_with_params(
            redirect_uri, {"state": state_to_pass, "error": e.get_codes(), "error_description": e.detail}
        )


@swagger_auto_schema(**MetagovSchemas.plugin_metadata)
@api_view(["GET"])
def plugin_metadata(request, plugin_name):
    cls = plugin_registry.get(plugin_name)
    if not cls:
        return HttpResponseBadRequest(f"No such plugin: {plugin_name}")

    return JsonResponse(
        {
            "name": cls.name,
            "auth_type": cls.auth_type,
            "uses_webhook": utils.plugin_uses_webhooks(cls),
            "schemas": {
                "config": cls.config_schema,
                "actions": utils.get_action_schemas(cls),
                "events": utils.get_event_schemas(cls),
                "processes": utils.get_process_schemas(cls),
            },
        }
    )


@swagger_auto_schema(**MetagovSchemas.plugin_schemas)
@api_view(["GET"])
def plugin_config_schemas(request):
    plugins = {}
    for (name, cls) in plugin_registry.items():
        plugins[name] = cls.config_schema
    return JsonResponse(plugins)


@csrf_exempt
@swagger_auto_schema(method="post", auto_schema=None)
@api_view(["POST"])
def receive_webhook(request, community, plugin_name, webhook_slug=None):
    """
    API endpoint for receiving webhook requests from external services
    """

    try:
        community = Community.objects.get(slug=community)
    except Community.DoesNotExist:
        return HttpResponseNotFound()

    # Lookup plugin
    plugin = get_plugin_instance(plugin_name, community)

    # Validate slug if the plugin has `webhook_slug` configured
    expected_slug = plugin.config.get(utils.WEBHOOK_SLUG_CONFIG_KEY)
    if webhook_slug != expected_slug:
        logger.error(f"Received request at {webhook_slug}, expected {expected_slug}. Rejecting.")
        return HttpResponseBadRequest()

    plugin_cls = plugin_registry[plugin_name]
    if plugin_cls._webhook_receiver_function:
        webhook_receiver = getattr(plugin, plugin_cls._webhook_receiver_function)
        logger.info(f"Passing webhook request to: {plugin}")
        try:
            webhook_receiver(request)
        except Exception as e:
            logger.error(f"Plugin '{plugin}' failed to process webhook: {e}")

    # Call `receive_webhook` on each of the GovernanceProcess proxy models
    proxy_models = plugin_cls._process_registry.values()
    for cls in proxy_models:
        processes = cls.objects.filter(plugin=plugin, status=ProcessStatus.PENDING.value)
        logger.info(f"{processes.count()} pending processes for plugin instance '{plugin}'")
        for process in processes:
            logger.info(f"Passing webhook request to: {process}")
            try:
                process.receive_webhook(request)
            except Exception as e:
                logger.error(e)

    return HttpResponse()


@csrf_exempt
@swagger_auto_schema(method="post", auto_schema=None)
@api_view(["POST"])
def receive_webhook_global(request, plugin_name):
    """
    API endpoint for receiving webhook requests from external services.
    For plugins that receive events for multiple communities to a single URL -- like Slack and Discord
    """
    # FIXME: figure out a better way to register the event processing function
    try:
        plugin_views = importlib.import_module(f"metagov.plugins.{plugin_name}.views")
    except ModuleNotFoundError:
        logger.error(f"no receiver for {plugin_name}")
        return HttpResponse()

    if not hasattr(plugin_views, "process_event"):
        logger.error(f"no receiver for {plugin_name}")
        return HttpResponse()

    logger.debug(f"Processing incoming event for {plugin_name}")
    return plugin_views.process_event(request)


def decorated_create_process_view(plugin_name, slug):
    # get process model proxy class
    cls = plugin_registry[plugin_name]._process_registry[slug]
    prefixed_slug = f"{plugin_name}.{slug}"
    """
    Decorate the `create_process_endpoint` view with swagger schema properties defined by the plugin author
    """

    @community_middleware
    @api_view(["POST"])
    def create_process(request):
        # Look up plugin instance (throws if plugin is not installed for this community)
        plugin = get_plugin_instance(plugin_name, request.community)
        payload = JSONParser().parse(request)
        callback_url = payload.pop("callback_url", None)  # pop to remove it

        # Validate payload
        if cls.input_schema:
            try:
                jsonschema.validate(payload, cls.input_schema)
            except jsonschema.exceptions.ValidationError as err:
                raise ValidationError(err.message)

        # Create new process instance
        new_process = cls.objects.create(name=slug, callback_url=callback_url, plugin=plugin)
        logger.info(f"Created process: {new_process}")

        # Start process
        try:
            new_process.start(payload)
        except APIException as e:
            new_process.delete()
            raise e
        except Exception as e:
            # Catch any other exceptions so that we can delete the model.
            new_process.delete()
            raise e

        logger.info(f"Started process: {new_process}")

        # return 202 with resource location in header
        response = HttpResponse(status=HTTPStatus.ACCEPTED)
        response["Location"] = f"/{utils.construct_process_url(plugin_name, slug)}/{new_process.pk}"
        return response

    request_body_schema = utils.json_schema_to_openapi_object(cls.input_schema) if cls.input_schema else {}

    return swagger_auto_schema(
        method="post",
        responses={
            202: "Process successfully started. Use the URL from the `Location` header in the response to get the status and outcome of the process."
        },
        operation_id=f"Start {prefixed_slug}",
        tags=[Tags.GOVERNANCE_PROCESS],
        operation_description=f"Start a new governance process of type '{prefixed_slug}'",
        manual_parameters=[MetagovSchemas.community_header],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "callback_url": openapi.Schema(
                    type=openapi.TYPE_STRING, description="URL to POST outcome to when process is completed"
                ),
                **request_body_schema.get("properties", {}),
            },
            required=request_body_schema.get("required", []),
        ),
    )(create_process)


def decorated_get_process_view(plugin_name, slug):
    # get process model proxy class
    cls = plugin_registry[plugin_name]._process_registry[slug]
    prefixed_slug = f"{plugin_name}.{slug}"

    @swagger_auto_schema(
        method="delete",
        operation_id=f"Close {prefixed_slug}",
        operation_description=f"Close the {prefixed_slug} process",
        tags=[Tags.GOVERNANCE_PROCESS],
    )
    @swagger_auto_schema(
        method="get",
        operation_id=f"Check status of {prefixed_slug}",
        operation_description=f"Poll the pending {prefixed_slug} governance process",
        tags=[Tags.GOVERNANCE_PROCESS],
        responses={
            200: openapi.Response(
                "Current process record. Check the `status` field to see if the process has completed. If the `errors` field has data, the process failed.",
                GovernanceProcessSerializer,
            ),
            404: "Process not found",
        },
    )
    @api_view(["GET", "DELETE"])
    def get_process(request, process_id):
        try:
            process = cls.objects.get(pk=process_id)
        except cls.DoesNotExist:
            return HttpResponseNotFound()

        # 'DELETE'  means close the process and return it. This will update process state.
        if request.method == "DELETE":
            if process.status == ProcessStatus.COMPLETED.value:
                raise ValidationError("Can't close process, it has already completed")
            try:
                logger.info(f"Closing: {process}")
                process.close()
            except NotImplementedError:
                raise APIException(
                    f"{process.plugin.name}.{process.name} does not support manually closing the process."
                )
            if process.status != ProcessStatus.COMPLETED.value:
                raise APIException("Failed to close process")

        serializer = GovernanceProcessSerializer(process)
        logger.info(f"Returning serialized process: {serializer.data}")
        return JsonResponse(serializer.data)

    return get_process


def decorated_perform_action_view(plugin_name, slug, tags=[]):
    cls = plugin_registry[plugin_name]
    meta = cls._action_registry[slug]
    prefixed_slug = f"{plugin_name}.{slug}"

    @community_middleware
    @api_view(["POST"])
    def perform_action(request):
        """
        Perform an action on a platform
        """
        # 1. Look up plugin instance
        plugin = get_plugin_instance(plugin_name, request.community)

        action_function = getattr(plugin, meta.function_name)

        # 2. Validate input parameters
        parameters = {}
        if request.method == "POST" and request.body:
            payload = JSONParser().parse(request)
            parameters = payload.get("parameters", {})
            # TODO: add back support for GET. Should be allowed if params are simple enough.
        if request.method == "GET":
            parameters = request.GET.dict()  # doesnt support repeated params 'a=2&a=3'
            utils.restruct(parameters)

        if meta.input_schema:
            try:
                jsonschema.validate(parameters, meta.input_schema)
            except jsonschema.exceptions.ValidationError as err:
                raise ValidationError(err.message)

        # 3. Invoke action function
        response = action_function(parameters)

        # 4. Validate response
        if meta.output_schema:
            try:
                jsonschema.validate(response, meta.output_schema)
            except jsonschema.exceptions.ValidationError as err:
                raise ValidationError(err.message)

        # 5. Return response
        try:
            return JsonResponse(response)
        except TypeError:
            logger.error(f"Failed to serialize {response}")
            raise

    arg_dict = {
        "method": "post",
        "operation_description": meta.description,
        "manual_parameters": [MetagovSchemas.community_header],
        "operation_id": prefixed_slug,
        "tags": tags or [Tags.ACTION],
    }
    if meta.input_schema:
        properties = {"parameters": utils.json_schema_to_openapi_object(meta.input_schema)}

        arg_dict["request_body"] = openapi.Schema(type=openapi.TYPE_OBJECT, properties={**properties})

    if meta.output_schema:
        arg_dict["responses"] = {200: utils.json_schema_to_openapi_object(meta.output_schema)}
    else:
        arg_dict["responses"] = {200: "action was performed successfully"}

    return swagger_auto_schema(**arg_dict)(perform_action)


def get_plugin_instance(plugin_name, community):
    """get the right proxy of a plugin instance"""
    cls = plugin_registry.get(plugin_name)
    if not cls:
        raise ValidationError(f"Plugin '{plugin_name}' not found")

    plugin = cls.objects.filter(name=plugin_name, community=community).first()
    if not plugin:
        raise ValidationError(f"Plugin '{plugin_name}' not enabled for community '{community}'")
    return plugin

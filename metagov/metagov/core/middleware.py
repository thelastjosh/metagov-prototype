import json
import logging
import traceback

from django.http import HttpResponseBadRequest, HttpResponseServerError
from metagov.core.models import Community
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

COMMUNITY_HEADER = "X-Metagov-Community"


class CommunityMiddleware:
    """
    Middleware for attaching Community to request object
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, *view_args, **view_kwargs):
        slug = request.headers.get(COMMUNITY_HEADER)
        if not slug:
            return HttpResponseBadRequest(f"Missing required header '{COMMUNITY_HEADER}'")
        try:
            community = Community.objects.get(slug=slug)
        except Community.DoesNotExist:
            return HttpResponseBadRequest(f"Community '{slug}' not found")
        request.community = community


def api_500_exception_handler(exception, context):
    """
    Handle uncaught exceptions and return them as 500s with the correct JSON shape (drf APIException)
    """
    response = exception_handler(exception, context)
    if response is None:
        logger.error(traceback.format_exc())
        logger.error(f"ERROR: {type(exception).__name__} {str(exception)} thrown in context '{context}'")
        return HttpResponseServerError(json.dumps({"detail": "A server error occurred."}))
    return response

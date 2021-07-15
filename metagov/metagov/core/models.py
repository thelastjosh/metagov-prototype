import logging
import time
from enum import Enum

import jsonpickle
import requests
import uuid
from django.conf import settings
from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

class AuthType:
    API_KEY = "api_key"
    OAUTH = "oauth"
    NONE = "none"

class Community(models.Model):
    slug = models.SlugField(
        max_length=36, default=uuid.uuid4, unique=True, help_text="Unique slug identifier for the community"
    )
    readable_name = models.CharField(max_length=100, blank=True, help_text="Human-readable name for the community")

    def __str__(self):
        if self.readable_name:
            return f"{self.readable_name} ({self.slug})"
        return self.slug


class DataStore(models.Model):
    datastore = models.JSONField(default=dict)

    def get(self, key):
        value = self.datastore.get(key)
        if value is not None:
            return jsonpickle.decode(value)
        return value

    def set(self, key, value):
        self.datastore[key] = jsonpickle.encode(value)
        self.save()
        return True

    def remove(self, key):
        res = self.datastore.pop(key, None)
        self.save()
        if not res:
            return False
        return True


class PluginManager(models.Manager):
    def get_queryset(self):
        qs = super(PluginManager, self).get_queryset()
        if self.model._meta.proxy:
            # this is a proxy model, so only return plugins of this proxy type
            return qs.filter(name=self.model.name)
        return qs


class Plugin(models.Model):
    """Represents an instance of an activated plugin."""

    name = models.CharField(max_length=30, blank=True, help_text="Name of the plugin")
    community = models.ForeignKey(
        Community, models.CASCADE, related_name="plugins", help_text="Community that this plugin instance belongs to"
    )
    config = models.JSONField(default=dict, null=True, blank=True, help_text="Configuration for this plugin instance")
    state = models.OneToOneField(DataStore, models.CASCADE, help_text="Datastore to persist any state", null=True)

    # Static metadata
    auth_type = AuthType.NONE
    config_schema = {}

    objects = PluginManager()

    class Meta:
        unique_together = ["name", "community"]

    def __str__(self):
        return f"{self.name} for '{self.community}'"

    def save(self, *args, **kwargs):
        if not self.pk:
            self.state = DataStore.objects.create()
            self.initialize()
        super(Plugin, self).save(*args, **kwargs)

    def initialize(self):
        """Initialize the plugin. Invoked once, when the plugin instance is created."""
        pass

    def send_event_to_driver(self, event_type: str, data: dict, initiator: dict):
        """Send an event to the driver"""
        if not settings.DRIVER_EVENT_RECEIVER_URL:
            return
        event = {
            "community": self.community.slug,
            "source": self.name,
            "event_type": event_type,
            "timestamp": str(time.time()),
            "data": data,
            "initiator": initiator,
        }
        serialized = jsonpickle.encode(event, unpicklable=False)
        logger.debug("Sending event to Driver: " + serialized)
        resp = requests.post(settings.DRIVER_EVENT_RECEIVER_URL, data=serialized)
        if not resp.ok:
            logger.error(
                f"Error sending event to driver at {settings.DRIVER_EVENT_RECEIVER_URL}: {resp.status_code} {resp.reason}"
            )


class ProcessStatus(Enum):
    CREATED = "created"
    PENDING = "pending"
    COMPLETED = "completed"


class GovernanceProcessManager(models.Manager):
    def get_queryset(self):
        qs = super(GovernanceProcessManager, self).get_queryset()
        if self.model._meta.proxy:
            # this is a proxy model, so only return processes of this proxy type
            return qs.filter(name=self.model.name, plugin__name=self.model.plugin_name)
        return qs


class GovernanceProcess(models.Model):
    """Represents an instance of a governance process."""

    name = models.CharField(max_length=30)
    callback_url = models.CharField(
        max_length=100, null=True, blank=True, help_text="Callback URL to notify when the process is updated"
    )
    status = models.CharField(
        max_length=15, choices=[(s.value, s.name) for s in ProcessStatus], default=ProcessStatus.CREATED.value
    )
    plugin = models.ForeignKey(
        Plugin, models.CASCADE, related_name="plugin", help_text="Plugin instance that this process belongs to"
    )
    state = models.OneToOneField(
        DataStore, models.CASCADE, help_text="Datastore to persist any internal state", null=True
    )
    errors = models.JSONField(default=dict, blank=True, help_text="Errors to serialize and send back to driver")
    outcome = models.JSONField(default=dict, blank=True, help_text="Outcome to serialize and send back to driver")

    # Optional: description of the governance process
    description = None
    # Optional: JSONSchema for start parameters object
    input_schema = None
    # Optional: JSONSchema for outcome object
    outcome_schema = None

    objects = GovernanceProcessManager()

    def __str__(self):
        return f"{self.plugin.name}.{self.name} for '{self.plugin.community.slug}' ({self.pk}, {self.status})"

    def save(self, *args, **kwargs):
        if not self.pk:
            self.state = DataStore.objects.create()
        super(GovernanceProcess, self).save(*args, **kwargs)

    def start(self, parameters):
        """(REQUIRED) Start the governance process.

        Most implementations of this function will:

        - Make a request to start a governance process in an external system

        - Store any data in ``outcome`` that should be returned to the Driver. For example, the URL for a voting process in another system.

        - Store any internal state in ``state``

        - If process was started successfully, set ``status`` to ``pending``.

        - If process failed to start, raise an exception of type ``PluginErrorInternal``.

        - Call ``self.save()`` to persist changes."""
        pass

    def close(self):
        """(OPTIONAL) Close the governance process.

        Most implementations of this function will:

        - Make a request to close the governance process in an external system

        - If the process was closed successfully, set ``status`` to ``completed`` and set the ``outcome``.

        - If the process failed to close, set ``errors`` or raise an exception of type ``PluginErrorInternal``.

        - Call ``self.save()`` to persist changes.
        """
        raise NotImplementedError

    def receive_webhook(self, request):
        """(OPTIONAL) Receive an incoming webhook from an external system. This is the preferred way to update the process state.

        Most implementations of this function will:

        - Check if the webhook request pertains to this process.

        - Update ``state``, ``status``, ``outcome``, and/or ``errors`` as needed.

        - Call ``self.save()`` to persist changes."""
        pass

    def update(self):
        """(OPTIONAL) Update the process outcome. This function will be invoked repeatedly from a scheduled task. It's only necessary to implement
        this function if you can't use webhooks to update the process state.

        Implementations of this function might:

        - Make a request to get the current status from an external system. OR,

        - Check if a closing condition has has been met. For example, if a voting process should be closed after a specified amount of time.

        - Update ``state``, ``status``, ``outcome``, and/or ``errors`` as needed.

        - Call ``self.save()`` to persist changes."""
        pass


@receiver(pre_save)
def pre_save_governance_process(sender, instance, **kwargs):
    """
    Pre-save signale for GovernanceProcesses.
    If the ``status`` was changed to ``completed``, OR if the ``outcome`` was changed,
    it will post the serialized process to the ``callback_url``."""

    # Need to check if this is a GovernanceProcess using subclass
    # instead of using `sender` because these are Proxy models.
    if not issubclass(sender, GovernanceProcess):
        return

    try:
        obj = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        pass  # process is new
    else:
        if not obj.status == instance.status and instance.status == ProcessStatus.COMPLETED.value:
            logger.debug(f"Status changed: {obj.status}->{instance.status}")
            notify_callback_url(instance)
        elif not obj.outcome == instance.outcome:
            logger.debug(f"Outcome changed: {obj.outcome} -> {instance.outcome}")
            notify_callback_url(instance)


def notify_callback_url(process: GovernanceProcess):
    """Notify the Driver that this GovernanceProess has completed or that the outcome has been updated."""
    if not process.callback_url:
        return
    logger.debug(f"Posting process to '{process.callback_url}':")

    from metagov.core.serializers import GovernanceProcessSerializer

    serializer = GovernanceProcessSerializer(process)
    logger.debug(serializer.data)
    resp = requests.post(process.callback_url, json=serializer.data)
    if not resp.ok:
        logger.error(f"Error posting outcome to callback url: {resp.status_code} {resp.text}")

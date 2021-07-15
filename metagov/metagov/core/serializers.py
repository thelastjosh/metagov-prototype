import logging
from metagov.core.models import Community, Plugin, GovernanceProcess, ProcessStatus
from rest_framework import serializers
from metagov.core import utils

logger = logging.getLogger(__name__)


class PluginSerializer(serializers.ModelSerializer):
    community = serializers.SerializerMethodField()
    webhook_url = serializers.SerializerMethodField()
    auth_type = serializers.SerializerMethodField()

    class Meta:
        model = Plugin
        # TODO: include date enabled and user who enabled it
        fields = ("id", "name", "config", "community", "webhook_url", "auth_type")

    def get_community(self, inst):
        return inst.community.slug

    def get_webhook_url(self, inst):
        return utils.construct_webhook_url(inst)

    def get_auth_type(self, inst):
        from metagov.core.plugin_decorators import plugin_registry

        cls = plugin_registry[inst.name]
        return cls.auth_type


class CommunitySerializer(serializers.ModelSerializer):
    plugins = PluginSerializer(many=True, required=False, allow_null=True)

    class Meta:
        model = Community
        fields = ("slug", "readable_name", "plugins")

    def update(self, instance, validated_data):
        plugins = validated_data.get("plugins") or []
        for data in plugins:
            utils.create_or_update_plugin(
                plugin_name=data.get("name"), plugin_config=data.get("config"), community=instance
            )

        # deactivate any plugins that are not present in `plugins` (that means they are being deactivated)
        active_plugins = [p.get("name") for p in plugins]
        plugins = Plugin.objects.filter(community=instance)
        for inst in plugins:
            if inst.name not in active_plugins:
                logger.info(f"Deactivating plugin '{inst}'")
                # TODO what happens to pending processes?
                inst.delete()

        instance.slug = validated_data.get("slug", instance.slug)
        instance.readable_name = validated_data.get("readable_name", instance.readable_name)
        instance.save()

        return instance

    def create(self, validated_data):
        logger.debug(f"Creating community from data: {validated_data}")
        plugins = validated_data.get("plugins") or []
        validated_data.pop("plugins", None)
        instance = Community.objects.create(**validated_data)
        for data in plugins:
            utils.create_or_update_plugin(
                plugin_name=data.get("name"), plugin_config=data.get("config"), community=instance
            )
        return instance


class GovernanceProcessSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    community = serializers.SerializerMethodField()
    # TODO nothing is actually validating this field
    status = serializers.ChoiceField(choices=[(s.value, s.name) for s in ProcessStatus])

    class Meta:
        model = GovernanceProcess
        fields = ("id", "name", "community", "status", "errors", "outcome")

    def get_name(self, inst):
        return f"{inst.plugin.name}.{inst.name}"

    def get_community(self, inst):
        return inst.plugin.community.slug

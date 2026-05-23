from __future__ import annotations

from rest_framework import serializers


class RecommendationItemSerializer(serializers.Serializer):
    property_guid = serializers.UUIDField()
    property_kind = serializers.CharField()
    similarity = serializers.FloatField()


class PersonalizedRecommendationsResponseSerializer(serializers.Serializer):
    recommendations = RecommendationItemSerializer(many=True)
    total = serializers.IntegerField()
    has_embedding = serializers.BooleanField()

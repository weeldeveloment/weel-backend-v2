from __future__ import annotations

import logging

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from users.authentication import ClientJWTAuthentication

from .knn import get_personalized_recommendations
from .serializers import RecommendationItemSerializer

logger = logging.getLogger(__name__)


class PersonalizedRecommendationsView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="getPersonalizedRecommendations",
        operation_summary="Get personalized property recommendations",
        operation_description=(
            "Returns KNN-based personalized property recommendations for the "
            "authenticated client. Uses pgvector cosine similarity on client and "
            "property embeddings built from booking history, reviews, and preferences."
        ),
        tags=["Property / Recommendations"],
        manual_parameters=[
            openapi.Parameter(
                "kind",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                enum=["apartment", "cottage"],
                description="Filter by property kind. Defaults to apartment.",
            ),
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                description="Number of recommendations to return (1-50). Defaults to 20.",
            ),
            openapi.Parameter(
                "from_date",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                format="date",
                description="Reference date for availability filtering (YYYY-MM-DD).",
            ),
        ],
        responses={
            200: RecommendationItemSerializer(many=True),
            401: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={"detail": openapi.Schema(type=openapi.TYPE_STRING)},
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        client_id = int(user.id)

        property_kind = (
            str(request.query_params.get("kind") or "apartment").strip().lower()
        )
        if property_kind not in {"apartment", "cottage"}:
            property_kind = "apartment"

        limit = request.query_params.get("limit")
        try:
            limit = max(1, min(int(limit or 20), 50))
        except (ValueError, TypeError):
            limit = 20

        from_date = request.query_params.get("from_date")

        results = get_personalized_recommendations(
            client_id=client_id,
            property_kind=property_kind,
            limit=limit,
            from_date=from_date,
        )

        serializer = RecommendationItemSerializer(results, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

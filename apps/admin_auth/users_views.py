from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView

from users.serializers import ClientProfileSerializer, PartnerProfileSerializer

from .authentication import AdminJWTAuthentication
from .permissions import IsAdminUser
from .raw_repository import count_users_by_role, list_users_by_role


class AdminUserPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminBaseUsersListView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    pagination_class = AdminUserPagination

    role: str = ""
    serializer_class = None
    search_columns: list[str] = []

    def get(self, request, *args, **kwargs):
        search = request.query_params.get("search")
        ordering = request.query_params.get("ordering")
        total = count_users_by_role(
            role=self.role,
            search=search,
            search_columns=self.search_columns,
        )

        users = list_users_by_role(
            role=self.role,
            search=search,
            ordering=ordering,
            limit=max(total, 1),
            offset=0,
            search_columns=self.search_columns,
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(users, request, view=self)
        serializer = self.serializer_class(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminClientsListView(AdminBaseUsersListView):
    """List all clients - admin only"""

    role = "client"
    serializer_class = ClientProfileSerializer
    search_columns = ["email", "first_name", "last_name", "phone_number"]


class AdminPartnersListView(AdminBaseUsersListView):
    """List all partners - admin only"""

    role = "partner"
    serializer_class = PartnerProfileSerializer
    search_columns = ["email", "first_name", "last_name", "phone_number", "username"]

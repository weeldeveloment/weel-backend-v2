from __future__ import annotations

from rest_framework.filters import OrderingFilter


SORT_TO_ORDERING = {
    "price_high": ["-order_price"],
    "price_low": ["order_price"],
    "rating_high": ["-average_rating"],
    "rating_low": ["average_rating"],
    "reviews_high": ["-comment_count"],
    "reviews_low": ["comment_count"],
    "title_asc": ["title"],
    "title_desc": ["-title"],
}


class PropertyOrderingFilter(OrderingFilter):
    def get_ordering(self, request, queryset, view):
        sort = request.query_params.get("sort", "").strip().lower()
        if sort and sort in SORT_TO_ORDERING:
            return SORT_TO_ORDERING[sort]
        return super().get_ordering(request, queryset, view)


class PropertyServiceFilter:
    pass


class PropertyFilter:
    pass

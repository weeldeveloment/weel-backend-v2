from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken

from users.tokens import TokenMetadata


def create_b2b_tokens(b2b_user: dict) -> dict:
    refresh = RefreshToken()
    access = AccessToken()

    common_claims = {
        TokenMetadata.TOKEN_SUBJECT: str(b2b_user["id"]),
        TokenMetadata.TOKEN_ISSUER: getattr(settings, "JWT_ISSUER", "weel-backend"),
        TokenMetadata.TOKEN_USER_TYPE: "b2b",
        "company_id": b2b_user["company_id"],
        "role": b2b_user.get("role", "performer"),
    }

    for key, value in common_claims.items():
        refresh[key] = value
        access[key] = value

    refresh[TokenMetadata.TOKEN_TYPE_CLAIM] = "refresh"
    access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"

    return {
        "refresh": str(refresh),
        "access": str(access),
    }

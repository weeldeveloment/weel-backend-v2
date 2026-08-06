from django.conf import settings
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import AccessToken

from shared import token_denylist
from users.tokens import CustomRefreshToken, TokenMetadata


def create_b2b_tokens(b2b_user: dict) -> dict:
    refresh = CustomRefreshToken()
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


B2B_CLAIMS_TO_COPY = (
    TokenMetadata.TOKEN_SUBJECT,
    TokenMetadata.TOKEN_ISSUER,
    TokenMetadata.TOKEN_USER_TYPE,
    "company_id",
    "role",
)


def rotate_b2b_tokens(refresh_token: str) -> dict:
    """Exchange a B2B refresh token for a fresh pair, revoking the old one.

    B2B logins issued a refresh token from day one but there was no endpoint
    to redeem it, so every session ended when the access token expired.
    """
    token = CustomRefreshToken(token=refresh_token)

    if token.get(TokenMetadata.TOKEN_USER_TYPE) != "b2b":
        raise InvalidToken("Not a B2B refresh token.")

    # Refresh tokens are single-use: rotation revokes them below.
    token_denylist.assert_not_revoked(token.payload)

    new_refresh = CustomRefreshToken()
    new_access = AccessToken()

    for claim in B2B_CLAIMS_TO_COPY:
        if claim in token:
            new_refresh[claim] = token[claim]
            new_access[claim] = token[claim]

    new_refresh[TokenMetadata.TOKEN_TYPE_CLAIM] = "refresh"
    new_access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"

    token.blacklist()

    return {"refresh": str(new_refresh), "access": str(new_access)}

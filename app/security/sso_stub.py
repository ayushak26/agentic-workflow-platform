"""
Microsoft SSO stub.

Real flow: Authorization Code Grant via Azure AD OIDC endpoint.
Stub flow: Accept username + role in request body, return a locally-signed JWT
           with the same claim structure the real flow would produce.

To wire real Azure AD:
  1. Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET in .env
  2. Replace this module with msal library Authorization Code flow
  3. Validate the id_token, extract preferred_username and groups claims
  4. Map AAD group GUIDs → local role strings
  5. Issue your own JWT (or trust AAD's access token directly)

No other file changes needed — the JWT claim structure is identical.
"""
from app.security.jwt_handler import create_access_token


def stub_login(username: str, role: str, session_id: str | None = None) -> str:
    """
    Issue a JWT as if Azure AD had authenticated the user.
    In production this function is never called directly;
    the OIDC callback handler calls create_access_token after
    validating the real id_token from Azure.
    """
    return create_access_token(subject=username, role=role, session_id=session_id)
#!/usr/bin/env python3
"""Fetch WaterGuru readings. Run at most once or twice a day.

FLOW (constants are public, from github.com/bdwilson/waterguru-api)
  1. Cognito SRP with email+password        -> IdToken
  2. cognito-identity get_id / get_credentials_for_identity, presenting that
     IdToken as a login                     -> temporary AWS credentials
  3. SigV4-signed POST to the prod-getDashboardView Lambda

  The reference implementation wraps this in Flask and Docker; none of that is
  needed for a daily cron, so this speaks to the same endpoints directly.

  It uses pycognito rather than warrant: warrant is unmaintained (last release
  2018) and does not import on modern Python.

  RATE LIMIT: the upstream project asks that this not run more than once or
  twice a day, so it is scheduled daily and never on the 15-minute sampler.

Credentials: ~/.waterguru, two lines — email, then password. Never printed.
"""
import base64, json, os, sys

from . import config

CRED = os.path.expanduser(config.load()["waterguru"]["credentials"])
REGION = "us-west-2"
POOL_ID = "us-west-2_icsnuWQWw"
IDENTITY_POOL_ID = "us-west-2:691e3287-5776-40f2-a502-759de65a8f1c"
CLIENT_ID = "7pk5du7fitqb419oabb3r92lni"
IDP = f"cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
LAMBDA_URL = (f"https://lambda.{REGION}.amazonaws.com"
              "/2015-03-31/functions/prod-getDashboardView/invocations")


def creds():
    """Vault first, then the old plaintext file.

    Both paths are kept so an install that has not migrated carries on working,
    and one that has never has the password on disk in the clear.
    """
    from . import vault
    try:
        user, pw, src = vault.credentials_for("waterguru", CRED)
    except vault.VaultError as e:
        sys.exit(str(e))
    if user and pw:
        return user, pw
    sys.exit(f"no WaterGuru credentials — add them on the Settings tab, or write "
             f"two lines (email, password) to {CRED} and chmod 600 it")


def jwt_claim(token, key):
    """Read a claim without verifying — we only need the username, and the
    token was just issued to us over TLS by the pool that signed it."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get(key)


def fetch():
    import boto3, requests
    from pycognito.aws_srp import AWSSRP
    from requests_aws4auth import AWS4Auth

    user, password = creds()
    idp = boto3.client("cognito-idp", region_name=REGION,
                       aws_access_key_id="", aws_secret_access_key="")
    srp = AWSSRP(username=user, password=password, pool_id=POOL_ID,
                 client_id=CLIENT_ID, client=idp)
    tokens = srp.authenticate_user()["AuthenticationResult"]
    id_token = tokens["IdToken"]
    user_id = jwt_claim(id_token, "cognito:username") or jwt_claim(id_token, "sub")

    ident = boto3.client("cognito-identity", region_name=REGION,
                         aws_access_key_id="", aws_secret_access_key="")
    iid = ident.get_id(IdentityPoolId=IDENTITY_POOL_ID)["IdentityId"]
    c = ident.get_credentials_for_identity(
        IdentityId=iid, Logins={IDP: id_token})["Credentials"]

    auth = AWS4Auth(c["AccessKeyId"], c["SecretKey"], REGION, "lambda",
                    session_token=c["SessionToken"])
    r = requests.post(LAMBDA_URL, auth=auth,
                      json={"userId": user_id, "clientType": "WEB_APP",
                            "clientVersion": "0.2.3"},
                      headers={"Content-Type": "application/x-amz-json-1.0"},
                      timeout=45)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    data = fetch()
    if "--raw" in sys.argv:
        print(json.dumps(data, indent=2)[:6000])
    else:
        # Structure is undocumented upstream; show the shape so the parser can
        # be written against what actually arrives rather than a guess.
        def shape(o, p="", d=0):
            if d > 3: return
            if isinstance(o, dict):
                for k, v in list(o.items())[:25]:
                    t = type(v).__name__
                    leaf = "" if isinstance(v, (dict, list)) else f" = {str(v)[:60]}"
                    print(f"  {p}{k} ({t}){leaf}")
                    shape(v, p + "  ", d + 1)
            elif isinstance(o, list):
                print(f"  {p}[{len(o)} items]")
                if o: shape(o[0], p + "  ", d + 1)
        shape(data)

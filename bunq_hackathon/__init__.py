__version__ = "0.1.0"
__url_base__ = "http://051b6d4f.ngrok.io"

import json
import os
import base64
import socket
import random
import functools
import string
from dateutil import parser
from datetime import datetime
from json import loads
from pathlib import Path

import requests
from bunq.sdk.client import Pagination
from bunq.sdk.context import ApiContext, ApiEnvironmentType, BunqContext
from bunq.sdk.exception import BunqException
from bunq.sdk.model.generated import endpoint
from bunq.sdk.model.generated.object_ import Amount, NotificationFilter, Pointer
from flask import Flask, abort, redirect, render_template, request

TEMPLATES_DIRECTORY = Path("templates")
SESSIONS_DIRECTORY = Path("sessions")


class BunqInterface:
    _BUNQ_CONF_PRODUCTION = "bunq-production.conf"
    _BUNQ_CONF_SANDBOX = "bunq-sandbox.conf"

    _MONETARY_ACCOUNT_STATUS_ACTIVE = "ACTIVE"

    _DEFAULT_COUNT = 10

    _POINTER_TYPE_EMAIL = "EMAIL"
    _CURRENCY_EURL = "EUR"

    def __init__(self, session_key):
        self.user = None
        self.session_key = session_key
        self.avatar = None

        self.setup_context()
        self.setup_current_user()

    def setup_context(self):
        if Path(self.determine_bunq_conf_filename()).is_file():
            pass
        else:
            sandbox_user = self.generate_new_sandbox_user()
            ApiContext(
                ApiEnvironmentType.SANDBOX, sandbox_user.api_key, socket.gethostname()
            ).save(self.determine_bunq_conf_filename())

        self._api_context = ApiContext.restore(self.determine_bunq_conf_filename())
        self._api_context.ensure_session_active()
        self._api_context.save(self.determine_bunq_conf_filename())

        BunqContext.load_api_context(self._api_context)

        self._user_context = BunqContext.user_context()

    def determine_bunq_conf_filename(self):
        return SESSIONS_DIRECTORY.joinpath(Path(self.session_key))

    def generate_new_sandbox_user(self):
        url = "https://sandbox.bunq.com/v1/sandbox-user"

        headers = {
            "x-bunq-client-request-id": "uniqueness-is-required",
            "cache-control": "no-cache",
            "x-bunq-geolocation": "0 0 0 0 NL",
            "x-bunq-language": "en_US",
            "x-bunq-region": "en_US",
        }

        response = requests.request("POST", url, headers=headers)

        if response.status_code is 200:
            response_json = json.loads(response.text)

            return endpoint.SandboxUser.from_json(
                json.dumps(response_json["Response"][0]["ApiKey"])
            )

        raise BunqException("Could not create new sandbox user.")

    def setup_current_user(self):
        user = endpoint.User.get().value.get_referenced_object()

        if (
            isinstance(user, endpoint.UserPerson)
            or isinstance(user, endpoint.UserCompany)
            or isinstance(user, endpoint.UserLight)
        ):
            self.user = user

    def get_all_monetary_account_active(self, count=_DEFAULT_COUNT):
        BunqContext._api_context = self._api_context
        BunqContext._user_context = self._user_context

        pagination = Pagination()
        pagination.count = count

        all_monetary_account_bank = endpoint.MonetaryAccountBank.list(
            pagination.url_params_count_only
        ).value
        all_monetary_account_bank_active = []

        for monetary_account_bank in all_monetary_account_bank:
            if monetary_account_bank.status == self._MONETARY_ACCOUNT_STATUS_ACTIVE:
                all_monetary_account_bank_active.append(monetary_account_bank)

        return all_monetary_account_bank_active

    def make_request(self, amount_string, description, recipient):
        BunqContext._api_context = self._api_context
        BunqContext._user_context = self._user_context

        endpoint.RequestInquiry.create(
            amount_inquired=Amount(amount_string, self._CURRENCY_EURL),
            counterparty_alias=Pointer(self._POINTER_TYPE_EMAIL, recipient),
            description=description,
            allow_bunqme=True,
        )

    def get_avatar(self):
        if not self.avatar:
            attachment = endpoint.AttachmentPublicContent.list(
                self.user.avatar.image[0].attachment_public_uuid
            )
            attachment_meta = endpoint.AttachmentPublic.get(
                self.user.avatar.image[0].attachment_public_uuid
            )
            self.avatar = "data:{};base64,{}".format(
                attachment_meta.value.attachment.content_type,
                (base64.b64encode(attachment.value)).decode(),
            )

        return self.avatar


def create_app(test_config=None):
    app = Flask(
        __name__, instance_relative_config=True, template_folder=TEMPLATES_DIRECTORY
    )

    app.config.from_mapping(SECRET_KEY="dev")

    app.jinja_env.globals["__version__"] = __version__
    app.jinja_env.globals["__name__"] = "`mgames"

    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    if test_config is None:
        app.config.from_pyfile("config.py", silent=True)
    else:
        app.config.from_mapping(test_config)

    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    sessions = {}
    session_challenges = {}
    session_challenges_map = {}

    def session_check(f):
        @functools.wraps(f)
        def decorated_function(*args, **kws):
            session_key = request.cookies.get("key", None)

            print("{} SC 1 {}".format(request.remote_addr, session_key))

            if session_key:
                bqi = sessions.get(session_key, None)

                print("{} SC 2 {}".format(request.remote_addr, session_key))

                if bqi:
                    print("{} SC 3 {}".format(request.remote_addr, session_key))
                    return f(bqi, *args, **kws)

            return redirect("/create_session")

        return decorated_function

    @app.route("/")
    def get_index():
        return redirect("/create_session")

    @app.route("/create_session")
    def get_create_session():
        session_key = request.cookies.get("key", None)

        if not session_key:
            session_key = random_string(40)

        bqi = BunqInterface(session_key)
        sessions[session_key] = bqi

        resp = redirect("/dashboard")
        resp.set_cookie("key", session_key)

        return resp

    @app.route("/dashboard")
    @session_check
    def get_dashboard(bqi):
        session_key = request.cookies.get("key", None)
        challenges = session_challenges.get(session_key, None)

        return render_template(
            "dashboard.html",
            user=bqi.user,
            monetary_accounts=bqi.get_all_monetary_account_active(),
            challenges=challenges or [],
            avatar=bqi.get_avatar(),
        )

    @app.route("/get_money/<amount>")
    @session_check
    def get_get_money(bqi, amount):
        try:
            amount = float(amount)

            if amount <= 0:
                return render_error(400, "Amount cannot be less than or equal to 0.")
            elif amount > 500:
                return render_error(400, "Amount is greater than 500.")

            bqi.make_request(
                str(amount), "{}, please?".format(amount), "sugardaddy@bunq.com"
            )

            return redirect("/dashboard")
        except ValueError:
            return render_error(400, "Amount is not a number.")

    challenge_templates = {
        "Rock Paper Scissors": {"template": "challenges/rps.html", "duration": 10},
        "Poppin'!": {"template": "challenges/poppin.html", "duration": 15},
        "Rollin'!": {"template": "challenges/rollin.html", "duration": 15},
        "Jumpin'!": {"template": "challenges/jumpin.html", "duration": 20},
    }

    @app.route("/create_challenge", methods=["GET"])
    @session_check
    def get_create_challenge(bqi):
        return render_template(
            "create_challenge.html",
            user=bqi.user,
            challenge_types=challenge_templates.keys(),
        )

    @app.route("/create_challenge", methods=["POST"])
    @session_check
    def post_create_challenge(bqi):
        print(request.form)
        if "challenge_type" in request.form and "wager_amount" in request.form:
            try:
                wager_amount = float(request.form.get("wager_amount"))

                if wager_amount < 1:
                    return render_error(400, "Wager amount cannot be less than 1.")
                elif wager_amount > 5:
                    return render_error(400, "Wager amount is greater than 5.")

                session_key = request.cookies.get("key", None)
                session_challenge_key = random_string(6)
                session_challenges_map[session_challenge_key] = session_key

                if not session_key in session_challenges:
                    session_challenges[session_key] = {}

                session_challenges[session_key][session_challenge_key] = {
                    "challenge_author": "{} {}".format(
                        bqi.user.first_name, bqi.user.last_name
                    ),
                    "challenge_author_id": bqi.user.id_,
                    "challenge_type": request.form.get("challenge_type"),
                    "wager_amount": wager_amount,
                    "created": datetime.now(),
                    "session_challenge_key": session_challenge_key,
                    "state": "waiting",
                }

                return redirect("/challenge_request/{}".format(session_challenge_key))
            except ValueError:
                return render_error(400, "Wager amount is not a number.")
        else:
            return render_error(400, "Missing fields.")

    @app.route("/challenge_request/<challenge_key>", methods=["GET"])
    @session_check
    def get_challenge_request(bqi, challenge_key):
        avatar = bqi.get_avatar()

        if challenge_key in session_challenges_map:
            challenge = session_challenges[session_challenges_map[challenge_key]][
                challenge_key
            ]

            if challenge["state"] == "waiting":
                return render_template(
                    "challenge_request.html",
                    user=bqi.user,
                    challenge=challenge,
                    avatar=avatar,
                )
            elif bqi.user.id_ in challenge["participants"]:
                if challenge["state"] == "running":
                    return redirect("/challenge/{}".format(challenge_key))
                elif challenge["state"] == "finished":
                    return redirect("/challenge_results/{}".format(challenge_key))

            return render_error(403, "Challenge cannot be participated in.")
        else:
            return render_error(404, "Challenge not found.")

    @app.route("/join_challenge/<challenge_key>", methods=["GET"])
    @session_check
    def get_join_challenge(bqi, challenge_key):
        avatar = bqi.get_avatar()

        if challenge_key in session_challenges_map:
            challenge = session_challenges[session_challenges_map[challenge_key]][
                challenge_key
            ]

            if not "participants" in challenge:
                challenge["participants"] = {}

            challenge["participants"][bqi.user.id_] = {
                "full_name": "{} {}".format(bqi.user.first_name, bqi.user.last_name),
                "participant_id": bqi.user.id_,
                "avatar": avatar,
                "result": None
            }

            return redirect("/challenge_request/{}".format(challenge_key))
        else:
            return render_error(404, "Challenge not found.")

    @app.route("/start_challenge/<challenge_key>", methods=["GET"])
    @session_check
    def get_start_challenge(bqi, challenge_key):
        avatar = bqi.get_avatar()

        if challenge_key in session_challenges_map:
            challenge = session_challenges[session_challenges_map[challenge_key]][
                challenge_key
            ]

            if bqi.user.id_ in challenge["participants"]:
                if len(challenge["participants"].keys()) >= 2:
                    challenge["state"] = "running"

                return redirect("/challenge_request/{}".format(challenge_key))
            else:
                return redirect("/dashboard")
        else:
            return render_error(404, "Challenge not found.")

    @app.route("/challenge/<challenge_key>", methods=["GET"])
    @session_check
    def get_challenge(bqi, challenge_key):
        avatar = bqi.get_avatar()

        if challenge_key in session_challenges_map:
            challenge = session_challenges[session_challenges_map[challenge_key]][
                challenge_key
            ]

            if bqi.user.id_ in challenge["participants"]:
                template = challenge_templates[challenge["challenge_type"]]
                return render_template(
                    "challenge.html",
                    user=bqi.user,
                    avatar=avatar,
                    challenge=challenge,
                    content=render_template(template["template"]),
                    challenge_duration=template["duration"],
                )
            else:
                return redirect("/dashboard")
        else:
            return render_error(404, "Challenge not found.")
    
    
    @app.route("/challenge_yield/<challenge_key>/<result>", methods=["GET"])
    @session_check
    def get_challenge_yield(bqi, challenge_key, result):
        avatar = bqi.get_avatar()

        if challenge_key in session_challenges_map:
            challenge = session_challenges[session_challenges_map[challenge_key]][
                challenge_key
            ]

            if bqi.user.id_ in challenge["participants"]:
                challenge["participants"][bqi.user.id_]["result"] = result

                return redirect("/challenge_results/{}".format(challenge_key))
            else:
                return redirect("/dashboard")
        else:
            return render_error(404, "Challenge not found.")
    
    
    @app.route("/challenge_results/<challenge_key>", methods=["GET"])
    @session_check
    def get_challenge_results(bqi, challenge_key):
        avatar = bqi.get_avatar()

        if challenge_key in session_challenges_map:
            challenge = session_challenges[session_challenges_map[challenge_key]][
                challenge_key
            ]

            if bqi.user.id_ in challenge["participants"]:
                return render_template(
                    "challenge_results.html",
                    user=bqi.user,
                    challenge=challenge,
                    avatar=avatar,
                )
            else:
                return redirect("/dashboard")
        else:
            return render_error(404, "Challenge not found.")

    @app.errorhandler(404)
    def handle_bad_request(e):
        return render_error(404, "Page not found.")

    @app.errorhandler(BunqException)
    def handle_bunq_exception(e):
        return render_error(500, str(e))

    @app.template_filter()
    def full_datetime(value):
        if not isinstance(value, (datetime)):
            value = parser.isoparse(value)

        return value.strftime("%Y-%m-%d %H:%M:%S")

    return app


def render_error(error_code, error_message):
    return (
        render_template(
            "error.html", error_code=error_code, error_message=error_message
        ),
        error_code,
    )


def random_string(stringLength=20):
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(stringLength))


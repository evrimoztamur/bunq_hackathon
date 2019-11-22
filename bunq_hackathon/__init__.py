__version__ = "0.1.0"

import os

from flask import Flask, render_template, abort, redirect
from json import loads
from pathlib import Path

TEMPLATES_DIRECTORY = Path("templates")


def create_app(test_config=None):
    app = Flask(
        __name__, instance_relative_config=True, template_folder=TEMPLATES_DIRECTORY
    )

    app.config.from_mapping(SECRET_KEY="dev")

    app.jinja_env.globals["__version__"] = __version__
    app.jinja_env.globals["__name__"] = "Hackthapp"

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

    @app.route("/")
    def get_index():
        return render_template("index.html")

    @app.errorhandler(404)
    def handle_bad_request(e):
        return render_error(404, "Page not found.")

    return app


def render_error(error_code, error_message):
    return (
        render_template(
            "error.html", error_code=error_code, error_message=error_message
        ),
        error_code,
    )

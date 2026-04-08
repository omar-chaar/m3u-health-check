from flask import Flask, request, jsonify, Response
import logging
import os
from m3uchecker.health_check import load_playlist, check_channels
from flasgger import Swagger
import time

app = Flask(__name__)

swagger = Swagger(app)

@app.route("/check_channels", methods=["POST"])
def check_channels_api():
    """
    Check Channels and Return Alive Playlist
    ---
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            source:
              type: string
            retry_delay:
              type: number
              default: 0.3
            max_workers:
              type: integer
              default: 10
            diagnostics_dir:
              type: string
              default: "diagnostics"
    responses:
      200:
        description: Returns M3U playlist of alive channels
      400:
        description: Invalid playlist source
      500:
        description: Failed to check channels
    """
    try:
        data = request.json
        source = data.get("source")
        retry_delay = data.get("retry_delay", 0.3)
        max_workers = data.get("max_workers", 10)
        diagnostics_dir = data.get("diagnostics_dir", "diagnostics")

        if not source:
            return jsonify({"error": "Invalid playlist source"}), 400

        results = check_channels(source, retry_delay, max_workers, diagnostics_dir)

        alive_channels_details = [r for r in results if r[2] == "ALIVE"]

        if not alive_channels_details:
            m3u_playlist_content = "#EXTM3U\n"
            response = Response(
                m3u_playlist_content, mimetype="application/vnd.apple.mpegurl"
            )
            response.headers["Content-Disposition"] = (
                "attachment; filename=alive_channels.m3u"
            )
            return response

        playlist_lines = ["#EXTM3U"]
        for channel_detail in alive_channels_details:
            extinf_line = channel_detail[3]
            channel_url = channel_detail[1]

            if extinf_line:
                playlist_lines.append(extinf_line)
            playlist_lines.append(channel_url)

        m3u_playlist_content = "\n".join(playlist_lines)

        response = Response(
            m3u_playlist_content, mimetype="application/vnd.apple.mpegurl"
        )
        response.headers["Content-Disposition"] = (
            "attachment; filename=alive_channels.m3u"
        )
        return response

    except Exception as e:
        logging.error(f"Error in check_channels_api: {e}")
        return jsonify({"error": str(e)}), 500


from m3uchecker.api.cache import (
    FINAL_PLAYLIST_FILE,
    CACHE_MAX_AGE_SECONDS,
    get_last_best_workers,
    trigger_refresh_async,
)


@app.route("/get_cached_playlist", methods=["GET"])
def get_cached_playlist_api():
    """
    Get Cached Playlist
    ---
    responses:
      200:
        description: Returns cached playlist file (may be stale)
      404:
        description: No cached playlist found
      500:
        description: Server error
    """
    try:
        if not os.path.exists(FINAL_PLAYLIST_FILE):
            refresh_started = trigger_refresh_async()
            return (
                jsonify(
                    {
                        "error": "No cached playlist found.",
                        "refresh_started": refresh_started,
                    }
                ),
                404,
            )

        is_stale = (
            time.time() - os.path.getmtime(FINAL_PLAYLIST_FILE)
        ) > CACHE_MAX_AGE_SECONDS
        refresh_started = trigger_refresh_async() if is_stale else False

        with open(FINAL_PLAYLIST_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        response = Response(content, mimetype="application/vnd.apple.mpegurl")
        response.headers["Content-Disposition"] = (
            "attachment; filename=final_channels.m3u"
        )
        response.headers["X-Cache-Stale"] = str(is_stale).lower()
        response.headers["X-Refresh-Started"] = str(refresh_started).lower()
        response.headers["X-Benchmark-Workers"] = str(get_last_best_workers())
        return response
    except Exception as e:
        logging.error(f"Error in get_cached_playlist_api: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"message": "pong"})


def main():
    app.run(host="0.0.0.0", port=5000, debug=True)


if __name__ == "__main__":
    main()

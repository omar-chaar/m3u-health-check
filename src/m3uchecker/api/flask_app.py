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


@app.route("/file_structure", methods=["GET"])
def file_structure_api():
    """
    Show project file structure in browser
    ---
    parameters:
      - name: depth
        in: query
        required: false
        type: integer
        default: 4
      - name: include_hidden
        in: query
        required: false
        type: boolean
        default: false
    responses:
      200:
        description: Returns an HTML view of the project structure
      500:
        description: Failed to build file structure
    """
    try:
        depth = request.args.get("depth", default=DEFAULT_TREE_MAX_DEPTH, type=int)
        include_hidden = request.args.get("include_hidden", "false").lower() == "true"

        if depth is None:
            depth = DEFAULT_TREE_MAX_DEPTH
        depth = max(0, min(depth, 12))

        tree_html = _build_file_tree(
            PROJECT_ROOT,
            max_depth=depth,
            include_hidden=include_hidden,
        )

        html = f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Project File Structure</title>
  <style>
    body {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; margin: 1.5rem; line-height: 1.35; }}
    h1 {{ margin: 0 0 0.5rem; }}
    .meta {{ color: #444; margin-bottom: 1rem; }}
    ul {{ list-style: none; margin: 0; padding-left: 1rem; border-left: 1px solid #ddd; }}
    li {{ margin: 0.1rem 0; white-space: nowrap; }}
  </style>
</head>
<body>
  <h1>Project File Structure</h1>
  <div class=\"meta\">Root: {escape(PROJECT_ROOT)} | Depth: {depth} | Include hidden: {str(include_hidden).lower()}</div>
  <ul>{tree_html}</ul>
</body>
</html>
"""
        return Response(html, mimetype="text/html")
    except Exception as e:
        logging.error(f"Error in file_structure_api: {e}")
        return jsonify({"error": str(e)}), 500


def main():
    app.run(host="0.0.0.0", port=5000, debug=True)


if __name__ == "__main__":
    main()

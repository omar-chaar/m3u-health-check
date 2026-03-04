from flask import Flask, request, jsonify, Response
import logging
import os
from m3u_health_check import load_playlist, check_channels
from flasgger import Swagger

app = Flask(__name__)

swagger = Swagger(app)


@app.route("/get_playlist_source", methods=["POST"])
def get_playlist_source_api():
    """
    Get Playlist Source
    ---
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            url:
              type: string
            file_path:
              type: string
    responses:
      200:
        description: Successfully retrieved the playlist source
      400:
        description: No valid source provided
    """
    try:
        data = request.json
        url = data.get("url", "")
        file_path = data.get("file_path", "")
        if url:
            return jsonify({"source": url})
        if file_path:
            return jsonify({"source": file_path})
        return jsonify({"error": "No valid source provided"}), 400
    except Exception as e:
        logging.error(f"Error in get_playlist_source_api: {e}")
        return jsonify({"error": str(e)}), 500


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


@app.route("/get_cached_playlist", methods=["GET"])
def get_cached_playlist_api():
    """
    Get Cached Playlist
    ---
    responses:
      200:
        description: Returns cached playlist file
      404:
        description: No cached playlist found
      500:
        description: Server error
    """
    try:
        final_file = output_dir / "final_channels."
        if not final_file.exists():
            return (
                jsonify(
                    {
                        "error": "No cached playlist found, use the /cache_playlist endpoint first."
                    }
                ),
                404,
            )
        content = final_file.read_text(encoding="utf-8")
        response = Response(content, mimetype="application/vnd.apple.mpegurl")
        response.headers["Content-Disposition"] = (
            "attachment; filename=final_channels.m3u"
        )
        return response
    except Exception as exception:
        logging.error(f"Error in get_cached_playlist_api: {exception}")
        return jsonify({"error": str(exception)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

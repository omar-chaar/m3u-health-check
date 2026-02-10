from flask import Flask, request, jsonify
import logging
from m3u_health_check import get_playlist_source, check_channels
from flasgger import Swagger

app = Flask(__name__)

swagger = Swagger(app)

@app.route('/get_playlist_source', methods=['POST'])
def get_playlist_source_api():
    """
    Get Playlist Source
    ---
    tags:
      - Playlist
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            url:
              type: string
              description: URL of the playlist
            file_path:
              type: string
              description: Path to the playlist file
    responses:
      200:
        description: Successfully retrieved the playlist source
        schema:
          type: object
          properties:
            source:
              type: string
              description: The playlist source
      400:
        description: No valid source provided
    """
    try:
        data = request.json
        url = data.get('url', '')
        file_path = data.get('file_path', '')
        if url:
            return jsonify({'source': url})
        if file_path:
            return jsonify({'source': file_path})
        return jsonify({'error': 'No valid source provided'}), 400
    except Exception as e:
        logging.error(f"Error in get_playlist_source_api: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/check_channels', methods=['POST'])
def check_channels_api():
    """
    Check Channels
    ---
    tags:
      - Channels
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            source:
              type: string
              description: Playlist source (URL or file path)
            retry_delay:
              type: number
              description: Delay between retries in seconds
              default: 0.3
            max_workers:
              type: integer
              description: Number of parallel checks
              default: 10
            diagnostics_dir:
              type: string
              description: Directory to save diagnostics
              default: "diagnostics"
    responses:
      200:
        description: Successfully checked channels
        schema:
          type: object
          properties:
            alive_channels:
              type: integer
              description: Number of alive channels
            unstable_channels:
              type: integer
              description: Number of unstable channels
            dead_channels:
              type: integer
              description: Number of dead channels
      400:
        description: Invalid playlist source
      500:
        description: Failed to check channels
    """
    try:
        data = request.json
        source = data.get('source')
        retry_delay = data.get('retry_delay', 0.3)
        max_workers = data.get('max_workers', 10)
        diagnostics_dir = data.get('diagnostics_dir', 'diagnostics')

        if not source:
            return jsonify({'error': 'Invalid playlist source'}), 400

        results = check_channels(source, retry_delay, max_workers, diagnostics_dir)

        alive_channels = [r for r in results if r[2] == 'ALIVE']
        unstable_channels = [r for r in results if r[2] == 'UNSTABLE']
        dead_channels = [r for r in results if r[2] == 'DEAD']

        return jsonify({
            'alive_channels': len(alive_channels),
            'unstable_channels': len(unstable_channels),
            'dead_channels': len(dead_channels)
        })
    except Exception as e:
        logging.error(f"Error in check_channels_api: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
# ChatFit MCP Google Drive Exporter Design

## Overview
A standalone Model Context Protocol (MCP) server for ChatFit. This server exposes a tool that extracts training records from the local SQLite database (`chatfit.db`), converts them into a CSV format, and automatically uploads the CSV to Google Drive using the Google Drive API. 

This enables MCP clients (like Claude Desktop) to orchestrate data exports to Google Sheets for visualization and advanced analysis.

## Architecture & Tech Stack
- **Protocol:** Model Context Protocol (MCP) using the official `mcp` Python SDK (FastMCP).
- **Database Access:** `sqlite3` built-in library reading from the local `chatfit.db`.
- **Google Drive Integration:** `google-api-python-client`, `google-auth-httplib2`, and `google-auth-oauthlib`.
- **Environment:** Runs as a standalone Python process, separate from the primary Telegram Bot.

## Components

### 1. Data Extractor
- Connects to `chatfit.db`.
- Executes a `JOIN` query across `training_sessions`, `practices`, and `training_sets` to produce a flattened view of training data.
- **Columns to Export:** 
  - Date (`training_sessions.date`)
  - Practice Name (`practices.name`)
  - Practice Type (`practices.type`)
  - RPE (`training_sessions.rpe`)
  - Set Number (`training_sets.set_number`)
  - Weight (`training_sets.weight`)
  - Reps (`training_sets.reps`)
  - Distance (`training_sets.distance`)
  - Duration (`training_sets.duration`)
- Converts the SQL result set directly into an in-memory CSV string using Python's `csv` module.

### 2. Google Drive Client
- Reads authentication credentials from a local `credentials.json` file (Service Account).
- Utilizes the `googleapiclient.discovery` module to build the Drive v3 service.
- Creates a new file on Google Drive with mimeType `text/csv` (or converts to Google Sheets `application/vnd.google-apps.spreadsheet` if desired).
- Returns the Google Drive webViewLink to the caller.

### 3. MCP Server Tool
- Exposes a single tool: `export_training_data_to_drive`.
- **Parameters:**
  - `days` (optional, integer): Number of days to look back. If not provided, exports all-time data.
- **Return Value:** 
  - A success message containing the URL to the uploaded Google Drive file.

## Data Flow
1. User requests visualization or data export from the MCP client.
2. MCP Client invokes `export_training_data_to_drive(days=X)`.
3. Server executes SQL query on `chatfit.db` filtered by `date >= date('now', '-X days')`.
4. Server generates CSV in-memory.
5. Server calls Google Drive API to upload the file.
6. Server returns the file URL to the MCP client.
7. MCP Client presents the link to the user.

## Error Handling
- **Database Missing:** If `chatfit.db` is not found, the tool returns a clear error message.
- **Credentials Missing:** If `credentials.json` is missing or invalid, the tool returns instructions on how to set up Google Cloud credentials.
- **API Errors:** Network or permission errors from Google Drive are caught and returned gracefully as text to the MCP client.

## Security & Privacy
- Read-only access to the local database.
- Google Drive credentials must be scoped strictly to `https://www.googleapis.com/auth/drive.file` (access only to files created by the app).
- No sensitive user data (passwords, tokens) are transmitted.
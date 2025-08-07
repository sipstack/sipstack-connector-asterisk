#!/bin/bash

# Asterisk Voicemail Mail Command Script
# This script processes voicemail emails, sends audio for transcription,
# and delivers enhanced emails with transcription

set -euo pipefail

# Configuration
API_ENDPOINT="${VOICEMAIL_API_ENDPOINT:-http://localhost:3030/v1/rt/voicemail}"
API_KEY="${VOICEMAIL_API_KEY:-${SK_KEY}}"
SENDMAIL_CMD="${SENDMAIL_CMD:-/usr/sbin/sendmail}"
TEMP_DIR="${TEMP_DIR:-/tmp}"
DEBUG="${DEBUG:-0}"

# Template configuration - Simple and predictable
# Users should copy email.template.default to one of these locations:
EMAIL_TEMPLATE="/etc/asterisk/voicemail-email.template"
EMAIL_TEMPLATE_ALT="/etc/asterisk/voicemail-email.conf"

# Check which template exists
if [ -f "$EMAIL_TEMPLATE" ]; then
    TEMPLATE_FILE="$EMAIL_TEMPLATE"
elif [ -f "$EMAIL_TEMPLATE_ALT" ]; then
    TEMPLATE_FILE="$EMAIL_TEMPLATE_ALT"
else
    TEMPLATE_FILE=""
fi

# Logging function
log() {
    if [ "$DEBUG" -eq 1 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> /var/log/voicemail-processor.log
    fi
}

# Cleanup function
cleanup() {
    if [ -n "${UUID:-}" ]; then
        rm -f "$TEMP_DIR/vm-$UUID"*
    fi
}

# Set trap for cleanup
trap cleanup EXIT

# Generate unique ID for this voicemail
UUID=$(uuidgen || date +%s)

# Save the entire email from stdin
EMAIL_FILE="$TEMP_DIR/vm-$UUID.eml"
cat > "$EMAIL_FILE"

log "Processing voicemail with UUID: $UUID"
log "Arguments: context=$1, extension=$2, msgnum=$3, emails=$4"
log "Template file: ${TEMPLATE_FILE:-none}"

# Extract the audio attachment
# Method 1: Try ripmime if available
if command -v ripmime &> /dev/null; then
    EXTRACT_DIR="$TEMP_DIR/vm-$UUID-extract"
    mkdir -p "$EXTRACT_DIR"
    ripmime -i "$EMAIL_FILE" -d "$EXTRACT_DIR" 2>/dev/null || true
    
    # Find the audio file
    AUDIO_FILE=$(find "$EXTRACT_DIR" -type f \( -name "*.wav" -o -name "*.WAV" -o -name "*.gsm" -o -name "*.mp3" \) | head -1)
else
    # Method 2: Manual extraction using sed/awk
    # Extract base64 encoded audio between Content-Disposition and boundary
    AUDIO_FILE="$TEMP_DIR/vm-$UUID.wav"
    
    # Find the audio attachment section and decode it
    awk '
        /Content-Type: audio\// { audio_section = 1 }
        /Content-Transfer-Encoding: base64/ { if (audio_section) base64_section = 1 }
        /^$/ { if (base64_section) { printing = 1; next } }
        /^--/ { if (printing) exit }
        printing { print }
    ' "$EMAIL_FILE" | base64 -d > "$AUDIO_FILE" 2>/dev/null || {
        log "Failed to extract audio using awk method"
        # Try alternative extraction
        sed -ne '/Content-Disposition.*attachment/,/^--/ p' "$EMAIL_FILE" | \
        sed '1,/^$/d;/^--/d' | \
        base64 -d > "$AUDIO_FILE" 2>/dev/null || true
    }
fi

# Verify audio file exists and has content
if [ ! -s "$AUDIO_FILE" ]; then
    log "ERROR: Failed to extract audio file"
    # Send original email without transcription
    cat "$EMAIL_FILE" | $SENDMAIL_CMD -t
    exit 0
fi

log "Audio file extracted: $AUDIO_FILE ($(stat -c%s "$AUDIO_FILE" 2>/dev/null || echo 0) bytes)"

# Parse voicemail metadata from email
# Try to extract from custom p= line first
P_LINE=$(grep '^p=' "$EMAIL_FILE" || echo "")

if [ -n "$P_LINE" ]; then
    # Parse custom format: p=name~date~caller~mailbox~msgnum~duration
    IFS='~' read -r VM_NAME VM_DATE VM_CALLERID VM_MAILBOX VM_MSGNUM VM_DURATION <<< "${P_LINE#p=}"
    log "Parsed custom format: name=$VM_NAME, date=$VM_DATE, caller=$VM_CALLERID, mailbox=$VM_MAILBOX, msgnum=$VM_MSGNUM, duration=$VM_DURATION"
else
    # Parse from standard email format
    VM_MAILBOX="$2"
    VM_MSGNUM="$3"
    VM_NAME=$(grep -E "^To:|Dear" "$EMAIL_FILE" | head -1 | sed 's/.*Dear \(.*\):.*/\1/;s/^To: *//;s/<.*//' | xargs)
    VM_CALLERID=$(grep -A2 "From:" "$EMAIL_FILE" | grep -v "^From: \"Asterisk" | grep -v "^--" | sed 's/.*"\(.*\)".*/\1/' | xargs || echo "Unknown")
    VM_DURATION=$(grep -E "Length:|Duration:" "$EMAIL_FILE" | sed 's/.*: *//;s/ *seconds.*//' | head -1 || echo "0")
    VM_DATE=$(grep "^Date:" "$EMAIL_FILE" | sed 's/^Date: *//' | head -1 || date)
    log "Parsed standard format: name=$VM_NAME, caller=$VM_CALLERID, mailbox=$VM_MAILBOX, msgnum=$VM_MSGNUM, duration=$VM_DURATION"
fi

# Extract email headers
TO_EMAIL=$(grep "^To:" "$EMAIL_FILE" | sed 's/^To: *//' | head -1)
FROM_EMAIL=$(grep "^From:" "$EMAIL_FILE" | sed 's/^From: *//' | head -1)
SUBJECT=$(grep "^Subject:" "$EMAIL_FILE" | sed 's/^Subject: *//' | head -1)

# Prepare metadata for API
METADATA=$(cat <<EOF
{
    "context": "$1",
    "extension": "$2",
    "mailbox": "${VM_MAILBOX:-$2}",
    "msgnum": "${VM_MSGNUM:-$3}",
    "caller_id": "${VM_CALLERID:-Unknown}",
    "duration": "${VM_DURATION:-0}",
    "date": "${VM_DATE:-$(date)}",
    "recipient_name": "${VM_NAME:-User}",
    "recipient_email": "${TO_EMAIL:-$4}",
    "uuid": "$UUID"
}
EOF
)

log "Sending to API: $API_ENDPOINT"

# Send to API for processing
# The API will process audio, generate transcription, and optionally return full HTML
API_RESPONSE_FILE="$TEMP_DIR/vm-$UUID-response.json"

HTTP_STATUS=$(curl -s -w "%{http_code}" -o "$API_RESPONSE_FILE" \
    --max-time 30 \
    -X POST \
    -H "Authorization: Bearer $API_KEY" \
    -H "Accept: application/json" \
    -F "audio=@$AUDIO_FILE" \
    -F "metadata=$METADATA" \
    -F "format=${EMAIL_FORMAT:-html}" \
    "$API_ENDPOINT" 2>/dev/null || echo "000")

log "API Response Status: $HTTP_STATUS"

if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "202" ]; then
    # Parse API response
    if command -v jq &> /dev/null; then
        RESPONSE_TYPE=$(jq -r '.type // "transcription"' "$API_RESPONSE_FILE" 2>/dev/null || echo "transcription")
        TRANSCRIPTION=$(jq -r '.transcription // ""' "$API_RESPONSE_FILE" 2>/dev/null || echo "")
        HTML_CONTENT=$(jq -r '.html // ""' "$API_RESPONSE_FILE" 2>/dev/null || echo "")
        EMAIL_SUBJECT=$(jq -r '.subject // ""' "$API_RESPONSE_FILE" 2>/dev/null || echo "")
    else
        # Fallback parsing without jq
        TRANSCRIPTION=$(grep -o '"transcription":"[^"]*"' "$API_RESPONSE_FILE" | cut -d'"' -f4 || echo "")
        HTML_CONTENT=$(grep -o '"html":"[^"]*"' "$API_RESPONSE_FILE" | cut -d'"' -f4 || echo "")
        RESPONSE_TYPE="transcription"
    fi
    
    log "Response type: $RESPONSE_TYPE, Transcription length: ${#TRANSCRIPTION}"
    
    if [ "$RESPONSE_TYPE" = "html" ] && [ -n "$HTML_CONTENT" ]; then
        # API returned complete HTML email
        # Replace the plain text body with HTML
        NEW_EMAIL_FILE="$TEMP_DIR/vm-$UUID-new.eml"
        
        # Copy headers and update content type
        sed '/^$/q' "$EMAIL_FILE" | \
        sed 's/Content-Type: text\/plain.*/Content-Type: multipart\/mixed; boundary="----voicemail-boundary"/' > "$NEW_EMAIL_FILE"
        
        # Add custom subject if provided
        if [ -n "$EMAIL_SUBJECT" ]; then
            sed -i "s/^Subject:.*/Subject: $EMAIL_SUBJECT/" "$NEW_EMAIL_FILE"
        fi
        
        # Build multipart message
        cat >> "$NEW_EMAIL_FILE" <<EOF

------voicemail-boundary
Content-Type: text/html; charset=UTF-8

$HTML_CONTENT

------voicemail-boundary
Content-Type: audio/wav; name="voicemail.wav"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="voicemail.wav"

$(base64 "$AUDIO_FILE")
------voicemail-boundary--
EOF
        
        # Send the new email
        cat "$NEW_EMAIL_FILE" | $SENDMAIL_CMD -t
        log "Sent HTML email with transcription"
        
    elif [ -n "$TRANSCRIPTION" ] && [ -n "$TEMPLATE_FILE" ]; then
        # Use template file to build HTML email
        log "Building email from template: $TEMPLATE_FILE"
        
        # Set up variables for template
        TITLE="Voicemail Message #${VM_MSGNUM}"
        FOOTER="Voicemail Transcription Service"
        
        # Check for mailbox alerts
        VM_ALERT=""
        VM_ALERT_CLASS=""
        if [ "${VM_MSGNUM:-0}" -ge 75 ]; then
            VM_ALERT="Warning: You are approaching your mailbox limit of 100 messages."
            VM_ALERT_CLASS="alert-warning"
        fi
        if [ "${VM_MSGNUM:-0}" -ge 100 ]; then
            VM_ALERT="Alert: You have reached your mailbox limit of 100 messages."
            VM_ALERT_CLASS="alert-danger"
        fi
        
        # Build HTML from template
        HTML_CONTENT=$(cat "$TEMPLATE_FILE" | \
            sed "s|{{TITLE}}|$TITLE|g" | \
            sed "s|{{VM_NAME}}|$VM_NAME|g" | \
            sed "s|{{VM_DATE}}|$VM_DATE|g" | \
            sed "s|{{VM_CALLERID}}|$VM_CALLERID|g" | \
            sed "s|{{VM_MAILBOX}}|$VM_MAILBOX|g" | \
            sed "s|{{VM_MSGNUM}}|$VM_MSGNUM|g" | \
            sed "s|{{VM_DURATION}}|$VM_DURATION|g" | \
            sed "s|{{TRANSCRIPTION}}|$TRANSCRIPTION|g" | \
            sed "s|{{FOOTER}}|$FOOTER|g" | \
            sed "s|{{VM_ALERT}}|$VM_ALERT|g" | \
            sed "s|{{VM_ALERT_CLASS}}|$VM_ALERT_CLASS|g")
        
        # Remove conditional blocks if no content
        if [ -z "$TRANSCRIPTION" ]; then
            HTML_CONTENT=$(echo "$HTML_CONTENT" | sed '/{{#if TRANSCRIPTION}}/,/{{\/if}}/d')
        else
            HTML_CONTENT=$(echo "$HTML_CONTENT" | sed 's/{{#if TRANSCRIPTION}}//g;s/{{\/if}}//g')
        fi
        if [ -z "$VM_ALERT" ]; then
            HTML_CONTENT=$(echo "$HTML_CONTENT" | sed '/{{#if VM_ALERT}}/,/{{\/if}}/d')
        else
            HTML_CONTENT=$(echo "$HTML_CONTENT" | sed 's/{{#if VM_ALERT}}//g;s/{{\/if}}//g')
        fi
        
        # Create email with HTML content
        NEW_EMAIL_FILE="$TEMP_DIR/vm-$UUID-new.eml"
        
        # Copy headers and update content type
        sed '/^$/q' "$EMAIL_FILE" | \
        sed 's/Content-Type: text\/plain.*/Content-Type: multipart\/mixed; boundary="----voicemail-boundary"/' | \
        sed '/^p=/d' > "$NEW_EMAIL_FILE"
        
        # Build multipart message
        cat >> "$NEW_EMAIL_FILE" <<EOF

------voicemail-boundary
Content-Type: text/html; charset=UTF-8

$HTML_CONTENT

------voicemail-boundary
Content-Type: audio/wav; name="voicemail.wav"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="voicemail.wav"

$(base64 "$AUDIO_FILE")
------voicemail-boundary--
EOF
        
        cat "$NEW_EMAIL_FILE" | $SENDMAIL_CMD -t
        log "Sent templated email with transcription"
        
    elif [ -n "$TRANSCRIPTION" ]; then
        # No template available - send simple HTML
        log "No template found, using simple HTML format"
        
        HTML_BODY="<html><body>"
        HTML_BODY+="<h2>Voicemail Message</h2>"
        HTML_BODY+="<p>Hello $VM_NAME,</p>"
        HTML_BODY+="<p>You received a new voicemail.</p>"
        HTML_BODY+="<table style='margin: 20px 0;'>"
        HTML_BODY+="<tr><td><strong>Date:</strong></td><td>$VM_DATE</td></tr>"
        HTML_BODY+="<tr><td><strong>From:</strong></td><td>$VM_CALLERID</td></tr>"
        HTML_BODY+="<tr><td><strong>Mailbox:</strong></td><td>$VM_MAILBOX</td></tr>"
        HTML_BODY+="<tr><td><strong>Duration:</strong></td><td>$VM_DURATION seconds</td></tr>"
        HTML_BODY+="</table>"
        HTML_BODY+="<h3>Transcription:</h3>"
        HTML_BODY+="<div style='background-color: #f0f0f0; padding: 10px; border-radius: 5px;'>"
        HTML_BODY+="$TRANSCRIPTION"
        HTML_BODY+="</div>"
        HTML_BODY+="<p><small><i>This transcription is provided as a convenience and may not be 100% accurate.</i></small></p>"
        HTML_BODY+="</body></html>"
        
        # Create new email with HTML
        NEW_EMAIL_FILE="$TEMP_DIR/vm-$UUID-new.eml"
        sed '/^$/q' "$EMAIL_FILE" | \
        sed 's/Content-Type: text\/plain.*/Content-Type: multipart\/mixed; boundary="----voicemail-boundary"/' | \
        sed '/^p=/d' > "$NEW_EMAIL_FILE"
        
        cat >> "$NEW_EMAIL_FILE" <<EOF

------voicemail-boundary
Content-Type: text/html; charset=UTF-8

$HTML_BODY

------voicemail-boundary
Content-Type: audio/wav; name="voicemail.wav"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="voicemail.wav"

$(base64 "$AUDIO_FILE")
------voicemail-boundary--
EOF
        
        cat "$NEW_EMAIL_FILE" | $SENDMAIL_CMD -t
        log "Sent simple HTML email with transcription"
    else
        # No transcription available - send original
        log "No transcription available, sending original email"
        cat "$EMAIL_FILE" | $SENDMAIL_CMD -t
    fi
else
    # API call failed - send original email
    log "API call failed with status $HTTP_STATUS, sending original email"
    cat "$EMAIL_FILE" | $SENDMAIL_CMD -t
fi

exit 0
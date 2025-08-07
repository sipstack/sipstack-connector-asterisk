#!/bin/bash
# SIPSTACK Recording Upload Script
# Uploads recordings older than 2 minutes to the API

set -e

# Configuration from environment
API_KEY="${API_KEY}"
REGION="${REGION:-us1}"
AMI_HOST="${AMI_HOST:-localhost}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Recording settings from environment
WATCH_PATHS="${RECORDING_WATCH_PATHS:-/var/spool/asterisk/monitor}"
FILE_EXTENSIONS="${RECORDING_FILE_EXTENSIONS:-wav,mp3,gsm}"
MIN_FILE_SIZE="${RECORDING_MIN_FILE_SIZE:-1024}"
DELETE_AFTER_UPLOAD="${RECORDING_DELETE_AFTER_UPLOAD:-false}"
MIN_AGE_MINUTES="${RECORDING_MIN_AGE_MINUTES:-2}"

# API endpoint based on region
case "$REGION" in
    "dev"|"ca1")
        API_BASE="https://api-dev.sipstack.com/v1"
        ;;
    "us1")
        API_BASE="https://api.sipstack.com/v1"
        ;;
    "us2")
        API_BASE="https://api-us2.sipstack.com/v1"
        ;;
    *)
        API_BASE="https://api.sipstack.com/v1"
        ;;
esac

API_URL="${API_BASE}/mqs/recording"

# Logging function
log() {
    local level=$1
    shift
    local message="$@"
    
    case "$LOG_LEVEL" in
        "DEBUG")
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message"
            ;;
        "INFO")
            if [[ "$level" != "DEBUG" ]]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message"
            fi
            ;;
        *)
            if [[ "$level" == "ERROR" || "$level" == "WARN" ]]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $message"
            fi
            ;;
    esac
}

# Check if API key is set
if [[ -z "$API_KEY" ]]; then
    log ERROR "API_KEY environment variable not set"
    exit 1
fi

# Convert comma-separated extensions to find pattern
build_extension_pattern() {
    local extensions="$1"
    local pattern=""
    
    IFS=',' read -ra EXT_ARRAY <<< "$extensions"
    for ext in "${EXT_ARRAY[@]}"; do
        # Remove leading dot if present
        ext="${ext#.}"
        if [[ -n "$pattern" ]]; then
            pattern="${pattern} -o -iname *.${ext}"
        else
            pattern="-iname *.${ext}"
        fi
    done
    
    echo "$pattern"
}

# Extract metadata from filename (handles custom Asterisk recording format)
extract_metadata() {
    local filepath="$1"
    local filename=$(basename "$filepath")
    
    # Handle custom Asterisk recording format: extension-context-YYYY-MM-DD-HH-MM-SS-phone-sequence-type-.version.WAV
    # Example: 100-1-2023-08-23-16-04-46-9055713100-0-tcm-.1.WAV
    
    local uniqueid=""
    local src_number=""
    local dst_number=""
    
    # Try to parse the custom format
    if [[ "$filename" =~ ^([0-9]+)-([0-9]+)-([0-9]{4})-([0-9]{2})-([0-9]{2})-([0-9]{2})-([0-9]{2})-([0-9]{2})-([0-9]+)-([0-9]+)-(.+)\.([0-9]+)\.WAV$ ]]; then
        local extension="${BASH_REMATCH[1]}"
        local context="${BASH_REMATCH[2]}"
        local year="${BASH_REMATCH[3]}"
        local month="${BASH_REMATCH[4]}"
        local day="${BASH_REMATCH[5]}"
        local hour="${BASH_REMATCH[6]}"
        local minute="${BASH_REMATCH[7]}"
        local second="${BASH_REMATCH[8]}"
        local phone_number="${BASH_REMATCH[9]}"
        local sequence="${BASH_REMATCH[10]}"
        
        # Create a timestamp-based uniqueid that could match CDRs
        # Convert datetime to Unix timestamp for uniqueid
        local timestamp=$(date -d "$year-$month-$day $hour:$minute:$second" +%s 2>/dev/null || echo "")
        if [[ -n "$timestamp" ]]; then
            uniqueid="${timestamp}.${sequence}"
        fi
        
        # Set source number and destination
        src_number="$phone_number"
        dst_number="$extension"  # The extension is likely the destination
        
    else
        # Fallback to original logic for other formats
        # Extract UniqueID - try multiple patterns
        uniqueid=$(echo "$filename" | grep -oE '[a-zA-Z0-9-]+[0-9]{10,}\.[0-9]+' || echo "")
        
        if [[ -z "$uniqueid" ]]; then
            uniqueid=$(echo "$filename" | grep -oE '[0-9]{10,}\.[0-9]+' || echo "")
        fi
        
        if [[ -z "$uniqueid" ]]; then
            uniqueid=$(echo "$filename" | grep -oE '[0-9]{10,}' | head -1 || echo "")
        fi
        
        # Extract source number (first 10-11 digit number)
        src_number=$(echo "$filename" | grep -oE '\b[0-9]{10,11}\b' | head -1 || echo "")
        
        # Extract destination (could be extension or number)
        local numbers=$(echo "$filename" | grep -oE '\b[0-9]{3,11}\b')
        local count=0
        for num in $numbers; do
            count=$((count + 1))
            if [[ $count -eq 2 ]]; then
                dst_number="$num"
                break
            fi
        done
    fi
    
    echo "${uniqueid}|${src_number}|${dst_number}"
}

# Upload a single recording
upload_recording() {
    local filepath="$1"
    local filename=$(basename "$filepath")
    local filesize=$(stat -c%s "$filepath" 2>/dev/null || echo "0")
    
    # Skip if file is too small
    if [[ $filesize -lt $MIN_FILE_SIZE ]]; then
        log DEBUG "Skipping small file: $filepath ($filesize bytes)"
        return 1
    fi
    
    log INFO "Processing recording: $filepath (size: $filesize bytes)"
    
    # Extract metadata
    IFS='|' read -r uniqueid src_number dst_number <<< "$(extract_metadata "$filepath")"
    
    # Use uniqueid as recording_id, or filename if not found
    local recording_id="${uniqueid:-$filename}"
    
    log DEBUG "Metadata - recording_id: $recording_id, src: $src_number, dst: $dst_number, uniqueid: $uniqueid"
    
    # Get version from VERSION file if available
    local version="0.8.1"
    if [[ -f /app/VERSION ]]; then
        version=$(cat /app/VERSION | tr -d '\n')
    fi
    
    # Prepare curl command with multipart form data
    local curl_cmd="curl -s -f -w '\n%{http_code}' -X POST"
    curl_cmd="$curl_cmd -H 'Authorization: Bearer $API_KEY'"
    curl_cmd="$curl_cmd -H 'User-Agent: SIPSTACK-Connector-Asterisk/$version'"
    
    # Add hostname header if available
    if [[ -n "$AMI_HOST" ]]; then
        curl_cmd="$curl_cmd -H 'X-Asterisk-Hostname: $AMI_HOST'"
    fi
    
    # Add form fields
    curl_cmd="$curl_cmd -F 'recording_id=$recording_id'"
    [[ -n "$src_number" ]] && curl_cmd="$curl_cmd -F 'src_number=$src_number'"
    [[ -n "$dst_number" ]] && curl_cmd="$curl_cmd -F 'dst_number=$dst_number'"
    [[ -n "$uniqueid" ]] && curl_cmd="$curl_cmd -F 'call_id=$uniqueid'"
    [[ -n "$uniqueid" ]] && curl_cmd="$curl_cmd -F 'linkedid=$uniqueid'"
    
    # Determine content type based on file extension
    local content_type="audio/wav"  # default
    case "${filename,,}" in  # Convert to lowercase for comparison
        *.mp3)
            content_type="audio/mpeg"
            ;;
        *.gsm)
            content_type="audio/wav"  # GSM files sent as wav for compatibility
            ;;
        *.wav)
            content_type="audio/wav"
            ;;
    esac
    
    # Add the audio file with proper content type
    curl_cmd="$curl_cmd -F 'audio=@$filepath;type=$content_type'"
    curl_cmd="$curl_cmd '$API_URL'"
    
    # Execute upload
    log DEBUG "Executing: $curl_cmd"
    response=$(eval $curl_cmd 2>&1)
    http_code=$(echo "$response" | tail -n1)
    response_body=$(echo "$response" | sed '$d')
    
    if [[ "$http_code" == "202" ]]; then
        log INFO "Successfully uploaded: $filepath"
        
        # Handle post-upload action
        if [[ "$DELETE_AFTER_UPLOAD" == "true" ]]; then
            rm -f "$filepath"
            log INFO "Deleted recording after upload: $filepath"
        else
            # Move to .processed subdirectory
            local dir=$(dirname "$filepath")
            local processed_dir="${dir}/.processed"
            mkdir -p "$processed_dir"
            mv "$filepath" "$processed_dir/"
            log INFO "Moved to processed: $processed_dir/$filename"
        fi
        
        return 0
    else
        log ERROR "Failed to upload $filepath: HTTP $http_code"
        log ERROR "Response: $response_body"
        return 1
    fi
}

# Main processing loop
main() {
    local total_found=0
    local total_uploaded=0
    local total_failed=0
    
    log INFO "Starting recording upload scan"
    log DEBUG "Watch paths: $WATCH_PATHS"
    log DEBUG "Extensions: $FILE_EXTENSIONS"
    log DEBUG "Min age: $MIN_AGE_MINUTES minutes"
    
    # Build find pattern for extensions
    local ext_pattern=$(build_extension_pattern "$FILE_EXTENSIONS")
    
    # Process each watch path
    IFS=',' read -ra PATHS <<< "$WATCH_PATHS"
    for watch_path in "${PATHS[@]}"; do
        # Trim whitespace
        watch_path=$(echo "$watch_path" | xargs)
        
        if [[ ! -d "$watch_path" ]]; then
            log WARN "Watch path does not exist: $watch_path"
            continue
        fi
        
        log DEBUG "Scanning: $watch_path"
        
        # Find recordings older than MIN_AGE_MINUTES
        # Exclude .processed directories
        while IFS= read -r -d '' filepath; do
            total_found=$((total_found + 1))
            
            if upload_recording "$filepath"; then
                total_uploaded=$((total_uploaded + 1))
            else
                total_failed=$((total_failed + 1))
            fi
            
        done < <(find "$watch_path" \
            -type f \
            -mmin +${MIN_AGE_MINUTES} \
            \( $ext_pattern \) \
            -not -path "*/.processed/*" \
            -print0 2>/dev/null)
    done
    
    log INFO "Recording upload complete - Found: $total_found, Uploaded: $total_uploaded, Failed: $total_failed"
}

# Run main function
main
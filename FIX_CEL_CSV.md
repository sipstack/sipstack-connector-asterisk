# Fix for CEL CSV Single Line Issue

## Problem
Asterisk is writing all CEL events to a single line in Master.csv because of an error in `/etc/asterisk/cel_custom.conf`

## Root Cause
The cel_custom.conf has two issues:
1. Missing closing brace `}` at the end of the mapping
2. Incorrect variable references in the last two fields:
   - `${CHANNEL(userdeftype)}` should be `${userdeftype}`
   - `${CHANNEL(extra)}` should be `${eventextra}`

## Solution

### Step 1: Backup Current Config
```bash
cp /etc/asterisk/cel_custom.conf /etc/asterisk/cel_custom.conf.backup
```

### Step 2: Fix the Configuration
Edit `/etc/asterisk/cel_custom.conf` and replace the Master.csv line in the `[mappings]` section with:

```
Master.csv => ${CSV_QUOTE(${eventtype})},${CSV_QUOTE(${eventtime})},${CSV_QUOTE(${CALLERID(name)})},${CSV_QUOTE(${CALLERID(num)})},${CSV_QUOTE(${CALLERID(ANI)})},${CSV_QUOTE(${CALLERID(RDNIS)})},${CSV_QUOTE(${CALLERID(DNID)})},${CSV_QUOTE(${CHANNEL(exten)})},${CSV_QUOTE(${CHANNEL(context)})},${CSV_QUOTE(${CHANNEL(channame)})},${CSV_QUOTE(${CHANNEL(appname)})},${CSV_QUOTE(${CHANNEL(appdata)})},${CSV_QUOTE(${CHANNEL(amaflags)})},${CSV_QUOTE(${CHANNEL(accountcode)})},${CSV_QUOTE(${CHANNEL(uniqueid)})},${CSV_QUOTE(${CHANNEL(linkedid)})},${CSV_QUOTE(${BRIDGEPEER})},${CSV_QUOTE(${userdeftype})},${CSV_QUOTE(${eventextra})}
```

### Step 3: Clear Old CSV File
```bash
# Move the old malformed file
mv /var/log/asterisk/cel-custom/Master.csv /var/log/asterisk/cel-custom/Master.csv.old

# Create new empty file with correct permissions
touch /var/log/asterisk/cel-custom/Master.csv
chown asterisk:asterisk /var/log/asterisk/cel-custom/Master.csv
```

### Step 4: Reload Asterisk CEL Module
```bash
# Reload CEL module
asterisk -rx "module reload cel_custom.so"

# Or if you prefer a full reload
asterisk -rx "core reload"
```

### Step 5: Verify
```bash
# Wait a minute for some calls to process, then check the file
tail -n 10 /var/log/asterisk/cel-custom/Master.csv

# Each line should now be a separate CEL event
wc -l /var/log/asterisk/cel-custom/Master.csv
```

## Result
After this fix:
- Each CEL event will be written on its own line
- The CSV file will be properly formatted
- The connector (both v0.13.32 and v0.13.33) will be able to parse the events correctly

## Note
The connector v0.13.33 includes enhanced parsing that can handle both the broken single-line format AND the proper multi-line format, so it will work either way. However, fixing the Asterisk configuration is the proper solution.
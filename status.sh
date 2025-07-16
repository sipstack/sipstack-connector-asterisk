#!/bin/bash
#
# SIPSTACK Asterisk Connector - System Status Checker
# 
# This script performs comprehensive checks of your Asterisk system
# to ensure everything is configured correctly for the SIPSTACK connector.
#

set -e

# Colors and symbols for visual appeal
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Symbols
CHECK="✓"
CROSS="✗"
WARNING="⚠"
INFO="ℹ"
ARROW="→"

# Print functions with visual styling
print_header() {
    echo -e "\n${BOLD}${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${BLUE}║${NC} ${BOLD}$1${NC}${BOLD}${BLUE}$(printf '%*s' $((62 - ${#1})) '')║${NC}"
    echo -e "${BOLD}${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}\n"
}

print_section() {
    echo -e "${BOLD}${PURPLE}▶ $1${NC}"
    echo -e "${PURPLE}$(printf '─%.0s' {1..50})${NC}"
}

print_success() {
    echo -e "  ${GREEN}${CHECK}${NC} $1"
}

print_error() {
    echo -e "  ${RED}${CROSS}${NC} $1"
}

print_warning() {
    echo -e "  ${YELLOW}${WARNING}${NC} $1"
}

print_info() {
    echo -e "  ${CYAN}${INFO}${NC} $1"
}

print_result() {
    local status=$1
    local message=$2
    local details=$3
    
    if [[ $status == "success" ]]; then
        print_success "$message"
        [[ -n $details ]] && echo -e "    ${CYAN}${ARROW}${NC} $details"
    elif [[ $status == "error" ]]; then
        print_error "$message"
        [[ -n $details ]] && echo -e "    ${RED}${ARROW}${NC} $details"
    elif [[ $status == "warning" ]]; then
        print_warning "$message"
        [[ -n $details ]] && echo -e "    ${YELLOW}${ARROW}${NC} $details"
    else
        print_info "$message"
        [[ -n $details ]] && echo -e "    ${CYAN}${ARROW}${NC} $details"
    fi
}

# Check functions
check_asterisk() {
    print_section "Asterisk Service"
    
    if command -v asterisk >/dev/null 2>&1; then
        print_result "success" "Asterisk binary found" "$(which asterisk)"
        
        # Check if Asterisk is running
        if pgrep asterisk >/dev/null; then
            print_result "success" "Asterisk process is running" "PID: $(pgrep asterisk)"
            
            # Get Asterisk version
            local version=$(asterisk -V 2>/dev/null | head -1 || echo "Unknown")
            print_result "info" "Asterisk version" "$version"
            
            return 0
        else
            print_result "error" "Asterisk process is not running" "Start with: systemctl start asterisk"
            return 1
        fi
    else
        print_result "error" "Asterisk not found" "Please install Asterisk 16+"
        return 1
    fi
}

check_ami() {
    print_section "AMI Configuration"
    
    # Check if AMI is listening
    local ami_port=5038
    if netstat -ln 2>/dev/null | grep ":$ami_port " >/dev/null || ss -ln 2>/dev/null | grep ":$ami_port " >/dev/null; then
        print_result "success" "AMI is listening on port $ami_port"
        
        # Check manager.conf
        local manager_conf="/etc/asterisk/manager.conf"
        if [[ -f "$manager_conf" ]]; then
            print_result "success" "manager.conf found" "$manager_conf"
            
            # Check if enabled
            if grep -q "^enabled\s*=\s*yes" "$manager_conf"; then
                print_result "success" "AMI enabled in configuration"
            else
                print_result "error" "AMI not enabled" "Set 'enabled = yes' in [general] section"
            fi
            
            # Check for SIPSTACK user section
            if grep -q "^\[manager-sipstack\]" "$manager_conf"; then
                print_result "success" "manager-sipstack user section found"
                
                # Check permissions
                local sipstack_section=$(sed -n '/^\[manager-sipstack\]/,/^\[/p' "$manager_conf" | head -n -1)
                if echo "$sipstack_section" | grep -q "read.*cdr"; then
                    print_result "success" "CDR read permission configured"
                else
                    print_result "warning" "CDR read permission missing" "Add 'cdr' to read permissions"
                fi
            else
                print_result "error" "manager-sipstack user not found" "Add [manager-sipstack] section to manager.conf"
            fi
        else
            print_result "error" "manager.conf not found" "$manager_conf missing"
        fi
    else
        print_result "error" "AMI not listening on port $ami_port" "Check manager.conf and restart Asterisk"
    fi
}

check_cdr() {
    print_section "CDR Configuration"
    
    if command -v asterisk >/dev/null 2>&1 && pgrep asterisk >/dev/null; then
        # Check CDR status
        local cdr_status=$(asterisk -rx "cdr show status" 2>/dev/null || echo "Failed to get CDR status")
        
        if [[ "$cdr_status" == *"Logging:                    Enabled"* ]]; then
            print_result "success" "CDR logging is enabled"
            
            # Check if cdr_manager is running (not suspended)
            if [[ "$cdr_status" == *"cdr_manager (suspended)"* ]]; then
                print_result "error" "CDR manager is suspended" "Check cdr_manager.conf and reload module"
            elif [[ "$cdr_status" == *"cdr_manager"* ]]; then
                print_result "success" "CDR manager is active"
            else
                print_result "warning" "CDR manager status unclear" "Run: asterisk -rx 'module show like cdr_manager'"
            fi
            
            # Check batch mode
            if [[ "$cdr_status" == *"Mode:                       Batch"* ]]; then
                print_result "info" "CDR in batch mode" "Good for performance"
            fi
            
        elif [[ "$cdr_status" == *"Logging:                    Disabled"* ]]; then
            print_result "error" "CDR logging is disabled" "Enable CDR in cdr.conf"
        else
            print_result "warning" "Could not determine CDR status" "$cdr_status"
        fi
        
        # Check cdr_manager.conf
        local cdr_manager_conf="/etc/asterisk/cdr_manager.conf"
        if [[ -f "$cdr_manager_conf" ]]; then
            print_result "success" "cdr_manager.conf found" "$cdr_manager_conf"
            
            if grep -q "^enabled\s*=\s*yes" "$cdr_manager_conf"; then
                print_result "success" "CDR manager enabled in configuration"
            else
                print_result "error" "CDR manager not enabled" "Set 'enabled = yes' in cdr_manager.conf"
            fi
        else
            print_result "warning" "cdr_manager.conf not found" "Create file with [general] enabled = yes"
        fi
        
        # Check cdr_manager module
        local module_status=$(asterisk -rx "module show like cdr_manager" 2>/dev/null)
        if [[ "$module_status" == *"cdr_manager.so"*"Running"* ]]; then
            print_result "success" "cdr_manager module is loaded and running"
        elif [[ "$module_status" == *"cdr_manager.so"* ]]; then
            print_result "warning" "cdr_manager module loaded but may not be running"
        else
            print_result "error" "cdr_manager module not loaded" "Run: asterisk -rx 'module load cdr_manager.so'"
        fi
    else
        print_result "error" "Cannot check CDR status" "Asterisk not running"
    fi
}

check_docker() {
    print_section "Docker Environment"
    
    if command -v docker >/dev/null 2>&1; then
        print_result "success" "Docker is installed" "$(docker --version)"
        
        # Check if Docker is running
        if docker info >/dev/null 2>&1; then
            print_result "success" "Docker daemon is running"
            
            # Check for SIPSTACK connector container
            if docker ps -a --format "table {{.Names}}" | grep -q "sipstack-connector"; then
                local status=$(docker ps --format "table {{.Names}}\t{{.Status}}" | grep sipstack-connector | awk '{print $2}')
                if [[ "$status" == "Up" ]]; then
                    print_result "success" "SIPSTACK connector container is running"
                else
                    print_result "warning" "SIPSTACK connector container exists but not running" "Status: $status"
                fi
            else
                print_result "info" "No SIPSTACK connector container found" "Ready for deployment"
            fi
            
            # Check for SIPSTACK image
            if docker images --format "table {{.Repository}}" | grep -q "sipstack/asterisk-connector"; then
                local image_info=$(docker images sipstack/asterisk-connector --format "table {{.Tag}}\t{{.CreatedAt}}" | tail -n +2 | head -1)
                print_result "success" "SIPSTACK connector image available" "$image_info"
            else
                print_result "info" "SIPSTACK connector image not found" "Will be downloaded on first run"
            fi
        else
            print_result "error" "Docker daemon not running" "Start with: systemctl start docker"
        fi
    else
        print_result "error" "Docker not installed" "Install Docker from https://docs.docker.com/install/"
    fi
}

check_network() {
    print_section "Network Connectivity"
    
    # Check AMI port accessibility
    if nc -z localhost 5038 2>/dev/null || timeout 2 bash -c "</dev/tcp/localhost/5038" 2>/dev/null; then
        print_result "success" "AMI port (5038) is accessible locally"
    else
        print_result "error" "Cannot connect to AMI port 5038" "Check Asterisk AMI configuration"
    fi
    
    # Check internet connectivity for Docker image download
    if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
        print_result "success" "Internet connectivity available"
    else
        print_result "warning" "Internet connectivity issues" "May affect Docker image downloads"
    fi
    
    # Check Docker Hub connectivity
    if timeout 5 curl -s https://registry-1.docker.io/v2/ >/dev/null 2>&1; then
        print_result "success" "Docker Hub is accessible"
    else
        print_result "warning" "Docker Hub connectivity issues" "May affect image downloads"
    fi
}

run_test_commands() {
    print_section "Test Commands"
    
    if command -v asterisk >/dev/null 2>&1 && pgrep asterisk >/dev/null; then
        print_info "Testing CDR submission..."
        
        # Force CDR submission
        local submit_result=$(asterisk -rx "cdr submit" 2>&1)
        if [[ $? -eq 0 ]]; then
            print_result "success" "CDR submit command executed" "$submit_result"
        else
            print_result "error" "CDR submit failed" "$submit_result"
        fi
        
        # Show current CDR status
        print_info "Current CDR status:"
        asterisk -rx "cdr show status" 2>/dev/null | grep -E "(Logging|Mode|cdr_manager|batch)" | while read line; do
            print_result "info" "$line"
        done
        
        # Show AMI connections
        print_info "Active AMI connections:"
        local ami_connections=$(asterisk -rx "manager show connected" 2>/dev/null)
        if [[ -n "$ami_connections" ]] && [[ "$ami_connections" != *"No AMI sessions"* ]]; then
            echo "$ami_connections" | tail -n +2 | while read line; do
                [[ -n "$line" ]] && print_result "info" "$line"
            done
        else
            print_result "info" "No active AMI connections"
        fi
    else
        print_result "warning" "Cannot run test commands" "Asterisk not running"
    fi
}

show_recommendations() {
    print_section "Recommendations"
    
    echo -e "${BOLD}${GREEN}Next Steps:${NC}"
    echo -e "  1. Fix any ${RED}errors${NC} shown above"
    echo -e "  2. Address ${YELLOW}warnings${NC} if needed"
    echo -e "  3. Deploy SIPSTACK connector:"
    echo -e "     ${CYAN}curl -O https://raw.githubusercontent.com/sipstack/sipstack-connector-asterisk/main/docker-compose.yml${NC}"
    echo -e "     ${CYAN}curl -O https://raw.githubusercontent.com/sipstack/sipstack-connector-asterisk/main/.env.example${NC}"
    echo -e "     ${CYAN}cp .env.example .env && nano .env${NC}"
    echo -e "     ${CYAN}docker-compose up -d${NC}"
    echo -e "  4. Monitor logs:"
    echo -e "     ${CYAN}docker-compose logs -f${NC}"
    echo -e "  5. Check metrics:"
    echo -e "     ${CYAN}curl http://localhost:8000/metrics${NC}"
    
    echo -e "\n${BOLD}${YELLOW}Common Issues:${NC}"
    echo -e "  • If CDR manager is suspended: reload cdr_manager module"
    echo -e "  • If AMI connection fails: check manager.conf permissions"
    echo -e "  • If container can't connect: use host networking"
    echo -e "  • For detailed logs: docker logs sipstack-connector"
}

# Main execution
main() {
    print_header "SIPSTACK Asterisk Connector - System Status Check"
    
    echo -e "${BOLD}Checking system configuration for SIPSTACK connector...${NC}\n"
    
    # Run all checks
    check_asterisk
    echo
    check_ami
    echo
    check_cdr
    echo
    check_docker
    echo
    check_network
    echo
    run_test_commands
    echo
    show_recommendations
    
    echo -e "\n${BOLD}${GREEN}Status check complete!${NC}"
    echo -e "${BOLD}${BLUE}For support: https://github.com/sipstack/sipstack-connector-asterisk/issues${NC}\n"
}

# Check if running as root for some commands
if [[ $EUID -eq 0 ]]; then
    echo -e "${YELLOW}${WARNING}${NC} Running as root - some checks may show different results for non-root user"
    echo
fi

# Run main function
main "$@"
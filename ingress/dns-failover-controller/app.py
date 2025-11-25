#!/usr/bin/env python3
"""
DNS Failover Controller v2
Polls actual DNS state and manages failover between Cloudflare Tunnel and VPS.
Improvements:
- Reconciliation loop polls DNS every 30s
- Verifies actual DNS state via DNS resolution
- Self-healing from DNS drift
- Alerts are triggers, but controller verifies state before acting
"""

import os
import logging
import json
import time
import socket
import threading
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, Tuple
from flask import Flask, request, jsonify
import requests
from dataclasses import dataclass, asdict
from kubernetes import client, config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


class DNSTarget(Enum):
    """DNS target states"""
    CLOUDFLARE_TUNNEL = "tunnel"
    VPS_FAILOVER = "vps"
    UNKNOWN = "unknown"


class SystemState(Enum):
    """System states for state machine"""
    PRIMARY_HEALTHY = "primary_healthy"
    PRIMARY_DEGRADED = "primary_degraded"
    FAILING_OVER = "failing_over"
    ON_FAILOVER = "on_failover"
    RECOVERING = "recovering"
    DUAL_FAILURE = "dual_failure"


@dataclass
class FailoverState:
    """Current state of the failover system"""
    current_target: DNSTarget
    system_state: SystemState
    last_change_time: str
    failover_count_24h: int = 0
    stabilization_start: Optional[str] = None
    last_alert_time: Optional[str] = None


class DNSChecker:
    """Checks actual DNS state via resolution"""

    def __init__(self, hostname: str, vps_ip: str):
        self.hostname = hostname
        self.vps_ip = vps_ip
        # Cloudflare's IP ranges (common ones)
        self.cloudflare_ip_prefixes = [
            "104.16.", "104.17.", "104.18.", "104.19.", "104.20.", "104.21.",
            "104.22.", "104.23.", "104.24.", "104.25.", "104.26.", "104.27.",
            "172.64.", "172.65.", "172.66.", "172.67.", "172.68.", "172.69.",
            "173.245.", "188.114.", "190.93.", "197.234.", "198.41."
        ]

    def get_actual_dns_target(self) -> DNSTarget:
        """
        Determine actual DNS target by resolving the hostname.
        Returns:
            DNSTarget.VPS_FAILOVER if resolves to VPS IP
            DNSTarget.CLOUDFLARE_TUNNEL if resolves to Cloudflare IPs
            DNSTarget.UNKNOWN if cannot determine
        """
        try:
            # Resolve hostname using system DNS
            ips = socket.getaddrinfo(self.hostname, None)
            resolved_ips = [ip[4][0] for ip in ips]

            logger.debug(f"DNS resolution for {self.hostname}: {resolved_ips}")

            # Check if any IP is the VPS IP
            if self.vps_ip in resolved_ips:
                return DNSTarget.VPS_FAILOVER

            # Check if any IP is in Cloudflare range
            for ip in resolved_ips:
                for prefix in self.cloudflare_ip_prefixes:
                    if ip.startswith(prefix):
                        return DNSTarget.CLOUDFLARE_TUNNEL

            logger.warning(f"DNS resolved to unknown IPs: {resolved_ips}")
            return DNSTarget.UNKNOWN

        except Exception as e:
            logger.error(f"Error resolving DNS for {self.hostname}: {e}")
            return DNSTarget.UNKNOWN


class CloudflareAPI:
    """Cloudflare API client for DNS management"""

    def __init__(self, api_token: str, zone_id: str):
        self.api_token = api_token
        self.zone_id = zone_id
        self.base_url = "https://api.cloudflare.com/client/v4"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    def get_dns_record(self, name: str) -> Optional[Dict[str, Any]]:
        """Get DNS record for a hostname"""
        url = f"{self.base_url}/zones/{self.zone_id}/dns_records"
        params = {"name": name}

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get("success") and result.get("result"):
                return result["result"][0]
            return None
        except Exception as e:
            logger.error(f"Error getting DNS record: {e}")
            return None

    def get_dns_target_from_api(self, hostname: str, vps_ip: str, tunnel_id: str) -> DNSTarget:
        """Determine DNS target by checking Cloudflare API"""
        record = self.get_dns_record(hostname)
        if not record:
            return DNSTarget.UNKNOWN

        record_type = record.get("type")
        content = record.get("content", "")

        if record_type == "A" and content == vps_ip:
            return DNSTarget.VPS_FAILOVER
        elif record_type == "CNAME" and tunnel_id in content:
            return DNSTarget.CLOUDFLARE_TUNNEL
        else:
            logger.warning(f"Unknown DNS config: type={record_type}, content={content}")
            return DNSTarget.UNKNOWN

    def update_to_tunnel(self, hostname: str, tunnel_id: str) -> bool:
        """Update DNS to point to Cloudflare Tunnel (CNAME)"""
        record = self.get_dns_record(hostname)
        if not record:
            logger.error(f"DNS record not found for {hostname}")
            return False

        record_id = record["id"]
        tunnel_cname = f"{tunnel_id}.cfargotunnel.com"

        # If already a CNAME to tunnel, no change needed
        if record["type"] == "CNAME" and record["content"] == tunnel_cname:
            logger.info(f"{hostname} already points to tunnel")
            return True

        # Update to CNAME
        url = f"{self.base_url}/zones/{self.zone_id}/dns_records/{record_id}"
        data = {
            "type": "CNAME",
            "name": hostname.split('.')[0],  # subdomain only
            "content": tunnel_cname,
            "proxied": True
        }

        try:
            response = requests.put(url, headers=self.headers, json=data, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                logger.info(f"✅ Updated {hostname} → CNAME {tunnel_cname}")
                return True
            else:
                logger.error(f"Cloudflare API error: {result}")
                return False
        except Exception as e:
            logger.error(f"Error updating DNS to tunnel: {e}")
            return False

    def update_to_vps(self, hostname: str, vps_ip: str) -> bool:
        """Update DNS to point to VPS (A record)"""
        record = self.get_dns_record(hostname)
        if not record:
            logger.error(f"DNS record not found for {hostname}")
            return False

        record_id = record["id"]

        # If already an A record to VPS with correct proxying, no change needed
        if record["type"] == "A" and record["content"] == vps_ip and record.get("proxied") == False:
            logger.info(f"{hostname} already points to VPS (proxied=False)")
            return True

        # Update to A record
        url = f"{self.base_url}/zones/{self.zone_id}/dns_records/{record_id}"
        data = {
            "type": "A",
            "name": hostname.split('.')[0],  # subdomain only
            "content": vps_ip,
            "proxied": False  # Must be False to return actual VPS IP for failover
        }

        try:
            response = requests.put(url, headers=self.headers, json=data, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                logger.info(f"✅ Updated {hostname} → A {vps_ip}")
                return True
            else:
                logger.error(f"Cloudflare API error: {result}")
                return False
        except Exception as e:
            logger.error(f"Error updating DNS to VPS: {e}")
            return False


class HealthChecker:
    """Checks health of cloudflared pods and connectivity"""

    def __init__(self):
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        self.k8s_core = client.CoreV1Api()

    def check_cloudflared_pods_healthy(self, min_pods: int = 2) -> bool:
        """Check if minimum number of cloudflared pods are running"""
        try:
            pods = self.k8s_core.list_namespaced_pod(
                namespace="ingress",
                label_selector="app=cloudflared"
            )

            running_pods = sum(1 for pod in pods.items if pod.status.phase == "Running")
            logger.debug(f"Cloudflared pods running: {running_pods}/{min_pods} required")

            return running_pods >= min_pods
        except Exception as e:
            logger.error(f"Error checking cloudflared pods: {e}")
            return False

    def check_tunnel_connectivity(self, url: str = "https://mirai.sogos.io") -> bool:
        """Check if public URL is accessible"""
        try:
            response = requests.get(url, timeout=5)
            return response.status_code < 500  # Accept any non-server-error
        except Exception as e:
            logger.debug(f"Tunnel connectivity check failed: {e}")
            return False


class FailoverController:
    """Main controller for DNS failover logic with reconciliation loop"""

    def __init__(self):
        # Configuration from environment
        self.hostname = os.getenv("HOSTNAME", "mirai.sogos.io")
        self.tunnel_id = os.getenv("TUNNEL_ID", "cb2a7768-4162-4da9-ac04-138fdecf3e3d")
        self.vps_ip = os.getenv("VPS_IP", "165.227.110.199")
        self.stabilization_failover_minutes = float(os.getenv("STABILIZATION_FAILOVER_MINUTES", "1.5"))
        self.stabilization_failback_minutes = float(os.getenv("STABILIZATION_FAILBACK_MINUTES", "10"))
        self.max_failovers_24h = int(os.getenv("MAX_FAILOVERS_24H", "3"))
        self.reconcile_interval = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "30"))
        self.dry_run = os.getenv("DRY_RUN", "false").lower() == "true"

        # Cloudflare API
        api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        zone_id = os.getenv("CLOUDFLARE_ZONE_ID")
        if not api_token or not zone_id:
            raise ValueError("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ZONE_ID must be set")

        self.cf_api = CloudflareAPI(api_token, zone_id)
        self.dns_checker = DNSChecker(self.hostname, self.vps_ip)
        self.health_checker = HealthChecker()

        # Kubernetes client
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        self.k8s_core = client.CoreV1Api()
        self.namespace = "ingress"
        self.configmap_name = "dns-failover-state"

        # State management
        self.state = self._load_state()
        self.state_lock = threading.Lock()

    def _load_state(self) -> FailoverState:
        """Load state from ConfigMap or initialize"""
        try:
            cm = self.k8s_core.read_namespaced_config_map(self.configmap_name, self.namespace)
            state_json = cm.data.get("state.json", "{}")
            state_dict = json.loads(state_json)

            # Convert string values back to enums
            state_dict["current_target"] = DNSTarget(state_dict.get("current_target", "tunnel"))
            state_dict["system_state"] = SystemState(state_dict.get("system_state", "primary_healthy"))

            logger.info(f"Loaded state from ConfigMap: {state_dict}")
            return FailoverState(**state_dict)
        except Exception as e:
            logger.warning(f"Could not load state from ConfigMap: {e}. Initializing to default.")
            return FailoverState(
                current_target=DNSTarget.CLOUDFLARE_TUNNEL,
                system_state=SystemState.PRIMARY_HEALTHY,
                last_change_time=datetime.utcnow().isoformat(),
                failover_count_24h=0
            )

    def _save_state(self):
        """Save state to ConfigMap"""
        try:
            # Convert state to dict with enum values as strings
            state_dict = asdict(self.state)
            state_dict["current_target"] = self.state.current_target.value
            state_dict["system_state"] = self.state.system_state.value

            state_json = json.dumps(state_dict, indent=2)

            # Update ConfigMap
            cm = self.k8s_core.read_namespaced_config_map(self.configmap_name, self.namespace)
            cm.data["state.json"] = state_json
            self.k8s_core.patch_namespaced_config_map(self.configmap_name, self.namespace, cm)

            logger.debug(f"Saved state to ConfigMap")
        except Exception as e:
            logger.error(f"Failed to save state to ConfigMap: {e}")

    def determine_desired_target(self) -> DNSTarget:
        """
        Determine desired DNS target based on actual system health.
        This is ground truth, not based on alerts.
        """
        pods_healthy = self.health_checker.check_cloudflared_pods_healthy(min_pods=2)

        if pods_healthy:
            # Primary is healthy, should use tunnel
            return DNSTarget.CLOUDFLARE_TUNNEL
        else:
            # Primary is down, should use VPS failover
            return DNSTarget.VPS_FAILOVER

    def reconcile(self):
        """
        Reconciliation loop - verifies actual DNS state matches desired state.
        This runs periodically and self-heals from drift.
        """
        with self.state_lock:
            try:
                # Step 1: Check actual DNS state (ground truth via DNS resolution)
                actual_target = self.dns_checker.get_actual_dns_target()

                # Step 2: Also verify via Cloudflare API for accuracy
                api_target = self.cf_api.get_dns_target_from_api(
                    self.hostname, self.vps_ip, self.tunnel_id
                )

                # Step 3: Determine desired target based on actual health
                desired_target = self.determine_desired_target()

                logger.debug(f"Reconcile: actual_dns={actual_target.value}, "
                           f"api={api_target.value}, desired={desired_target.value}, "
                           f"state={self.state.current_target.value}")

                # Step 4: Check for drift between API and our state
                if api_target != DNSTarget.UNKNOWN and api_target != self.state.current_target:
                    logger.warning(f"⚠️ State drift detected! API shows {api_target.value} "
                                 f"but state says {self.state.current_target.value}")
                    # Update our state to match reality
                    self.state.current_target = api_target
                    self._save_state()

                # Step 5: If desired != actual and we're not stabilizing, initiate change
                if desired_target != self.state.current_target:
                    if self.state.stabilization_start:
                        # Already stabilizing, check if period has passed
                        self._check_stabilization_and_execute()
                    else:
                        # Start stabilization
                        if desired_target == DNSTarget.VPS_FAILOVER:
                            logger.warning(f"🔥 Detected tunnel failure - initiating failover")
                            self._start_failover_stabilization()
                        else:
                            logger.info(f"✅ Detected tunnel recovery - initiating failback")
                            self._start_failback_stabilization()

                # Step 6: If we're stabilizing, check if period has passed
                elif self.state.stabilization_start:
                    self._check_stabilization_and_execute()

            except Exception as e:
                logger.error(f"Error in reconciliation: {e}", exc_info=True)

    def _start_failover_stabilization(self):
        """Start stabilization period for failover"""
        if self.state.failover_count_24h >= self.max_failovers_24h:
            logger.error(f"⛔ Circuit breaker! {self.state.failover_count_24h} failovers in 24h")
            return

        self.state.stabilization_start = datetime.utcnow().isoformat()
        self.state.system_state = SystemState.PRIMARY_DEGRADED
        self._save_state()
        logger.info(f"⏱️ Starting {self.stabilization_failover_minutes}min failover stabilization")

    def _start_failback_stabilization(self):
        """Start stabilization period for failback"""
        self.state.stabilization_start = datetime.utcnow().isoformat()
        self.state.system_state = SystemState.RECOVERING
        self._save_state()
        logger.info(f"⏱️ Starting {self.stabilization_failback_minutes}min failback stabilization")

    def _check_stabilization_and_execute(self):
        """Check if stabilization period complete and execute DNS change"""
        if not self.state.stabilization_start:
            return

        stabilization_start = datetime.fromisoformat(self.state.stabilization_start)
        elapsed = datetime.utcnow() - stabilization_start

        # Determine required stabilization time
        if self.state.system_state == SystemState.PRIMARY_DEGRADED:
            required = timedelta(minutes=self.stabilization_failover_minutes)
            action = "failover"
        elif self.state.system_state == SystemState.RECOVERING:
            required = timedelta(minutes=self.stabilization_failback_minutes)
            action = "failback"
        else:
            return

        if elapsed < required:
            remaining = (required - elapsed).total_seconds() / 60
            logger.debug(f"⏱️ Stabilizing for {action}... {remaining:.1f}min remaining")
            return

        # Stabilization complete - execute change
        if action == "failover":
            self._execute_failover()
        else:
            self._execute_failback()

    def _execute_failover(self):
        """Execute failover to VPS"""
        logger.warning(f"🚨 Executing failover to VPS")

        if self.dry_run:
            logger.info("DRY RUN: Would switch DNS to VPS")
        else:
            success = self.cf_api.update_to_vps(self.hostname, self.vps_ip)
            if not success:
                logger.error("Failed to update DNS to VPS")
                return

        self.state.current_target = DNSTarget.VPS_FAILOVER
        self.state.system_state = SystemState.ON_FAILOVER
        self.state.last_change_time = datetime.utcnow().isoformat()
        self.state.failover_count_24h += 1
        self.state.stabilization_start = None
        self._save_state()

        logger.info("✅ Failover to VPS complete")

    def _execute_failback(self):
        """Execute failback to Cloudflare Tunnel"""
        logger.info(f"🔄 Executing failback to Cloudflare Tunnel")

        if self.dry_run:
            logger.info("DRY RUN: Would switch DNS back to Cloudflare Tunnel")
        else:
            success = self.cf_api.update_to_tunnel(self.hostname, self.tunnel_id)
            if not success:
                logger.error("Failed to update DNS to tunnel")
                return

        self.state.current_target = DNSTarget.CLOUDFLARE_TUNNEL
        self.state.system_state = SystemState.PRIMARY_HEALTHY
        self.state.last_change_time = datetime.utcnow().isoformat()
        self.state.stabilization_start = None
        self._save_state()

        logger.info("✅ Failback to Cloudflare Tunnel complete")

    def reconciliation_loop(self):
        """Background thread that runs reconciliation periodically"""
        logger.info(f"Starting reconciliation loop (interval={self.reconcile_interval}s)")
        while True:
            try:
                self.reconcile()
            except Exception as e:
                logger.error(f"Reconciliation loop error: {e}", exc_info=True)
            time.sleep(self.reconcile_interval)

    def handle_alert(self, alert_data: Dict[str, Any]) -> Dict[str, str]:
        """
        Handle incoming alert webhook.
        Alerts are now treated as triggers to speed up detection,
        but reconciliation loop verifies actual state.
        """
        alerts = alert_data.get("alerts", [])

        for alert in alerts:
            labels = alert.get("labels", {})
            status = alert.get("status", "")
            alert_name = labels.get("alertname", "")

            logger.info(f"Received alert: {alert_name} ({status}) - triggering reconciliation")

        # Trigger immediate reconciliation instead of acting directly
        threading.Thread(target=self.reconcile, daemon=True).start()

        return {"status": "ok", "action": "triggered_reconciliation"}


# Initialize controller
controller = FailoverController()


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "current_target": controller.state.current_target.value,
        "system_state": controller.state.system_state.value
    })


@app.route('/webhook', methods=['POST'])
def webhook():
    """Alertmanager webhook endpoint"""
    try:
        alert_data = request.get_json()
        logger.info(f"Received webhook: {json.dumps(alert_data, indent=2)}")

        result = controller.handle_alert(alert_data)
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/state', methods=['GET'])
def get_state():
    """Get current state"""
    with controller.state_lock:
        state_dict = asdict(controller.state)
        # Convert enums to strings for JSON serialization
        state_dict['current_target'] = state_dict['current_target'].value if isinstance(state_dict['current_target'], DNSTarget) else state_dict['current_target']
        state_dict['system_state'] = state_dict['system_state'].value if isinstance(state_dict['system_state'], SystemState) else state_dict['system_state']
        return jsonify(state_dict)


@app.route('/reconcile', methods=['POST'])
def trigger_reconcile():
    """Manually trigger reconciliation (for testing)"""
    threading.Thread(target=controller.reconcile, daemon=True).start()
    return jsonify({"status": "ok", "action": "triggered"})


if __name__ == '__main__':
    logger.info(f"Starting DNS Failover Controller v2 for {controller.hostname}")
    logger.info(f"Dry run mode: {controller.dry_run}")
    logger.info(f"Reconciliation interval: {controller.reconcile_interval}s")

    # Start reconciliation loop in background thread
    reconcile_thread = threading.Thread(target=controller.reconciliation_loop, daemon=True)
    reconcile_thread.start()

    # Start Flask app
    app.run(host='0.0.0.0', port=8080)

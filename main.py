import asyncio
import logging
import struct
import serial
import time
import platform
import random
import math
import json
import uvicorn
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any

# --- CONFIGURATION ---
if platform.system() == "Windows":
    SERIAL_PORT = 'COM3' 
else:
    SERIAL_PORT = '/dev/ttyUSB0'

BAUD_RATE = 9600
POLL_RATE = 1.0 

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("BAS_Core")

# --- DATA MODELS & STATE ---
class SystemState:
    def __init__(self):
        self.start_time = time.time()
        self.authenticated_sessions = set()
        self.site_config = {
            "name": "Headquarters", 
            "address": "101 Automation Blvd", 
            "phone": "555-0199",
            "image": "",
            "floorplan": ""
        }
        self.global_settings = {
            "emergency_stop": False,
            "boiler_lockout_temp": 35.0,
            "system_mode": "LIVE", # LIVE, DEMO, INSTALL
            "holidays": {}
        }
        # Credentials
        self.users = [{"username": "admin", "password": "admin", "role": "admin"}] 
        self.logs = []
        self.schedules = {
            "sch_default": {
                "id": "sch_default", "name": "Standard Office", 
                "days": {i: {"enabled": True, "start": "08:00", "end": "18:00"} for i in range(7)}
            }
        }
        
        # Initialize default unit
        self.units = {
            "rtu_1": {
                "id": "rtu_1", "name": "RTU-1 (Server Room)", "type": "RTU", "state": "IDLE",
                "temp": 74.5, "dat_val": 55.0, "secondary_val": 450, "secondary_type": "CO2 (ppm)",
                "setpoints": {"occ_cool": 72, "occ_heat": 68, "unocc_cool": 85, "unocc_heat": 60},
                "outputs": {"fan": False, "cool": False, "heat": False, "damper": 20},
                "inputs": {"fan_status": True, "filter_status": True, "alarm_general": False},
                "overrides": {},
                "alarms": [], "alarms_enabled": True,
                "is_occupied": True, "is_simulating": False,
                "history": [], 
                "pins": {"fan": 0, "cool": 1, "heat": 2, "damper": 3},
                "custom_sensors": {}, 
                "custom_sensor_values": {},
                "modbus_addr": 1, "modbus_reg_temp": 101, "modbus_reg_co2": 102, "temp_offset": 0.0,
                "mqtt_topic": "bas/rtu1", "bacnet_ip": "", "bacnet_obj": "",
                "x": 20, "y": 30, "image": ""
            }
        }
        self.global_occupied = True # Default state

    def add_log(self, type, unit, msg):
        self.logs.insert(0, {"ts": time.time(), "type": type, "unit": unit, "msg": msg})
        if len(self.logs) > 100: self.logs.pop()

sys = SystemState()

# --- HARDWARE DRIVER (HEX ENGINE) ---
class HexEngine:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None
        self.lock = asyncio.Lock()
        self.connected = False
        self.last_connect_attempt = 0

    def connect(self):
        if time.time() - self.last_connect_attempt < 5: return
        self.last_connect_attempt = time.time()
        try:
            if self.ser: self.ser.close()
            self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
            self.connected = True
            logger.info(f"✅ Hardware Connected: {self.port}")
        except Exception as e:
            if self.connected: logger.error(f"❌ Hardware Lost: {e}")
            self.connected = False

    def send_relay(self, relay_idx, state):
        if not self.connected: return
        try:
            data = 0xFF00 if state else 0x0000
            packet = struct.pack('>BBHH', 0xFF, 0x05, relay_idx, data)
            crc = 0xFFFF
            for byte in packet:
                crc ^= byte
                for _ in range(8):
                    crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
            final = packet + struct.pack('<H', crc)
            self.ser.write(final)
        except:
            self.connected = False

hw = HexEngine(SERIAL_PORT, BAUD_RATE)

# --- CONTROL LOOP & SIMULATION ---
async def control_loop():
    logger.info("🚀 BAS Logic Engine Started")
    hw.connect()
    
    while True:
        try:
            if not hw.connected: hw.connect()
            current_time = datetime.now()
            
            # --- 1. Global Schedule Calculation ---
            # Determine if building should be occupied based on "sch_default"
            day_idx = current_time.weekday() # 0=Mon
            sched = sys.schedules.get("sch_default")
            calc_occupied = False
            
            if sched and str(day_idx) in sched["days"]:
                d = sched["days"][str(day_idx)]
                if d["enabled"]:
                    try:
                        s_h, s_m = map(int, d["start"].split(":"))
                        e_h, e_m = map(int, d["end"].split(":"))
                        now_min = current_time.hour * 60 + current_time.minute
                        start_min = s_h * 60 + s_m
                        end_min = e_h * 60 + e_m
                        
                        # Check if within range (inclusive start, exclusive end)
                        if start_min <= now_min < end_min:
                            calc_occupied = True
                    except ValueError:
                        pass # Ignore schedule format errors
            
            # Update global state so Dashboard sees it
            sys.global_occupied = calc_occupied

            # --- 2. Process Units ---
            for uid, u in sys.units.items():
                # A. Apply Occupancy
                u["is_occupied"] = calc_occupied

                # B. Physics Simulation
                sim_change = 0.05
                if u["outputs"]["cool"]: sim_change -= 0.3
                if u["outputs"]["heat"]: sim_change += 0.4
                u["temp"] += sim_change
                u["temp"] = round(u["temp"], 1)

                # C. Control Logic
                sp_cool = u["setpoints"]["occ_cool"] if u["is_occupied"] else u["setpoints"]["unocc_cool"]
                sp_heat = u["setpoints"]["occ_heat"] if u["is_occupied"] else u["setpoints"]["unocc_heat"]
                
                req_cool = u["temp"] > sp_cool + 1.0
                req_heat = u["temp"] < sp_heat - 1.0
                # Fan logic: On if occupied OR if heating/cooling is needed
                req_fan = u["is_occupied"] or req_cool or req_heat

                if sys.global_settings["emergency_stop"]:
                    req_cool = req_heat = req_fan = False
                    u["state"] = "EMERGENCY STOP"
                else:
                    if req_cool: u["state"] = "COOLING"
                    elif req_heat: u["state"] = "HEATING"
                    elif req_fan: u["state"] = "FAN ONLY"
                    else: u["state"] = "OFF"

                u["outputs"]["fan"] = req_fan
                u["outputs"]["cool"] = req_cool
                u["outputs"]["heat"] = req_heat
                u["outputs"]["damper"] = 20 if req_fan else 0

                # D. Overrides
                for k, v in u["overrides"].items():
                    if k in u["outputs"]: u["outputs"][k] = v

                # E. Hardware Output (Map RTU_1 to Board)
                if uid == "rtu_1" and hw.connected:
                    async with hw.lock:
                        # Map Fan->0, Cool->1, Heat->2
                        to_send = [(0, u["outputs"]["fan"]), (1, u["outputs"]["cool"]), (2, u["outputs"]["heat"])]
                        for r_idx, val in to_send:
                            val_bool = True if val else False
                            await asyncio.to_thread(hw.send_relay, r_idx, val_bool)
                            await asyncio.sleep(0.05)

                # F. History
                u["history"].append({"ts": time.time(), "temp": u["temp"], "sp": sp_cool if req_cool else sp_heat, "out": 100 if (req_cool or req_heat) else 0})
                if len(u["history"]) > 60: u["history"].pop(0)

                # G. Alarms
                if u["alarms_enabled"]:
                    if u["temp"] > 85.0:
                        if not any(a["key"] == "high_temp" for a in u["alarms"]):
                            u["alarms"].append({"key": "high_temp", "msg": "High Temp Alarm (>85F)", "ts": time.time(), "acked": False})
                            sys.add_log("ALARM", u["name"], "High Temp Detected")
                    elif u["temp"] < 84.0:
                        # Auto-clear alarm
                        original_count = len(u["alarms"])
                        u["alarms"] = [a for a in u["alarms"] if a["key"] != "high_temp"]
                        if len(u["alarms"]) < original_count:
                            sys.add_log("NORMAL", u["name"], "High Temp Returned to Normal")

            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Loop Error: {e}")
            await asyncio.sleep(1.0)

# --- API ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(control_loop())
    yield
    if hw.ser: hw.ser.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token or token not in sys.authenticated_sessions: return None
    return "admin"

@app.get("/")
async def serve_dash(): return FileResponse("index.html")

@app.get("/api/status")
async def api_status(request: Request):
    user = get_current_user(request)
    return {
        "authenticated": user is not None,
        "username": user or "Guest",
        "role": "admin" if user else "viewer",
        "outdoor": 65.0,
        "global_occupied": sys.global_occupied,
        "site": sys.site_config,
        "global_settings": sys.global_settings,
        "units": list(sys.units.values()),
        "schedules": list(sys.schedules.values()),
        "users": sys.users if user else []
    }

@app.post("/api/login")
async def api_login(req: dict, response: Response):
    for u in sys.users:
        if u["username"] == req.get("username") and u["password"] == req.get("password"):
            token = f"auth_{int(time.time())}"
            sys.authenticated_sessions.add(token)
            response.set_cookie(key="session_token", value=token)
            return {"status": "ok"}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie("session_token")
    return {"status": "ok"}

# --- SYSTEM ENDPOINTS ---
@app.post("/api/system/occupancy")
async def api_set_occ(occupied: bool):
    # This might get overwritten by the loop in 1s, but we'll allow it for manual toggling
    sys.global_occupied = occupied
    sys.add_log("AUDIT", "System", f"Global Occupancy set to {occupied}")
    return {"status": "ok"}

@app.post("/api/system/reload")
async def api_reload():
    sys.add_log("AUDIT", "System", "System Reloaded")
    return {"status": "ok"}

@app.post("/api/settings")
async def api_settings(req: dict):
    sys.global_settings.update(req)
    return {"status": "ok"}

# --- UNIT ENDPOINTS ---
@app.post("/api/unit/{uid}/override")
async def api_override(uid: str, req: dict):
    if uid in sys.units:
        val, key = req.get("value"), req.get("key")
        if val is None: 
            if key in sys.units[uid]["overrides"]: del sys.units[uid]["overrides"][key]
        else: sys.units[uid]["overrides"][key] = val
    return {"status": "ok"}

@app.post("/api/unit/{uid}/setpoint")
async def api_setpoint(uid: str, req: dict):
    if uid in sys.units: sys.units[uid]["setpoints"][req.get("key")] = req.get("value")
    return {"status": "ok"}

@app.post("/api/unit/{uid}/ack")
async def api_ack(uid: str, req: dict):
    key = req.get("alarm_key")
    if uid in sys.units:
        for a in sys.units[uid]["alarms"]:
            if a["key"] == key: a["acked"] = True
    return {"status": "ok"}

@app.post("/api/unit/{uid}/alarms/config")
async def api_alarm_cfg(uid: str, req: dict):
    if uid in sys.units: sys.units[uid]["alarms_enabled"] = req.get("enabled")
    return {"status": "ok"}

@app.post("/api/unit/{uid}/layout")
async def api_layout(uid: str, req: dict):
    if uid in sys.units:
        sys.units[uid]["x"] = req.get("x")
        sys.units[uid]["y"] = req.get("y")
    return {"status": "ok"}

@app.post("/api/unit/{uid}/image")
async def api_u_img(uid: str, req: dict):
    if uid in sys.units: sys.units[uid]["image"] = req.get("image")
    return {"status": "ok"}

@app.post("/api/unit/{uid}/pin")
async def api_pin(uid: str, req: dict):
    if uid in sys.units: sys.units[uid]["pins"][req.get("key")] = req.get("pin")
    return {"status": "ok"}

@app.post("/api/unit/{uid}/net")
async def api_net(uid: str, req: dict):
    if uid in sys.units: sys.units[uid].update(req)
    return {"status": "ok"}

@app.post("/api/unit/{uid}/points")
async def api_points(uid: str, req: dict):
    if uid in sys.units:
        action, name, val = req.get("action"), req.get("name"), req.get("register")
        if action == "add": sys.units[uid]["custom_sensors"][name] = val
        elif action == "delete": 
            if name in sys.units[uid]["custom_sensors"]: del sys.units[uid]["custom_sensors"][name]
    return {"status": "ok"}

# --- ADMIN ENDPOINTS ---
@app.post("/api/admin/site")
async def update_site(req: dict):
    sys.site_config.update(req)
    return {"status": "ok"}

@app.post("/api/units")
async def create_unit(req: dict):
    new_id = f"unit_{int(time.time())}"
    sys.units[new_id] = {
        "id": new_id, "name": req.get("name"), "type": req.get("type", "RTU"),
        "state": "OFF", "temp": 72.0, "dat_val": None, "secondary_val": None, "secondary_type": "",
        "setpoints": {"occ_cool":74,"occ_heat":68,"unocc_cool":80,"unocc_heat":60},
        "outputs": {"fan":False,"cool":False,"heat":False, "damper": 0},
        "inputs": {}, "overrides": {}, "alarms": [], "alarms_enabled": True, 
        "history": [], "is_occupied": False, "is_simulating": False,
        "pins": {}, "custom_sensors": {}, "custom_sensor_values": {},
        "modbus_addr": req.get("modbus_addr", 1), "image": "", "x": 50, "y": 50
    }
    return {"status": "ok"}

@app.delete("/api/units/{uid}")
async def del_unit(uid: str):
    if uid in sys.units: del sys.units[uid]
    return {"status": "ok"}

@app.post("/api/schedules")
async def save_sched(req: dict):
    sys.schedules[req.get("id")] = req
    return {"status": "ok"}

@app.get("/api/history/{uid}")
async def api_history(uid: str):
    if uid in sys.units: return sys.units[uid]["history"]
    return []

@app.get("/api/logs")
async def api_logs(): return sys.logs

@app.get("/api/platform")
async def api_platform():
    return {
        "hostname": platform.node(),
        "os": platform.system(),
        "uptime": str(timedelta(seconds=int(time.time() - sys.start_time))),
        "server_time": datetime.now().strftime("%H:%M:%S"),
        "cpu_sim": "15%", "memory_sim": "40%"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

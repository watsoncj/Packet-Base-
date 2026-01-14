import json
import queue
from datetime import datetime
import re

# Regex to strip ANSI escape sequences
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

import tkinter as tk
from tkinter import ttk, messagebox

from serial.tools import list_ports

import meshtastic
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

# ======================= COLORS / THEME ====================
BG_MAIN = "#05030a"
BG_PANEL = "#070b18"
BG_HEADER = "#0c1024"
FG_TEXT = "#d0ffea"
FG_MUTED = "#6aa3b4"
FG_WARN = "#ffcc33"
FG_ERR = "#ff3366"
FG_OK = "#43ff9a"
ACCENT = "#25b8ff"
ACCENT_SOFT = "#144b6a"
FONT_MAIN = ("Consolas", 10)
FONT_SMALL = ("Consolas", 9)
FONT_HEADER = ("Consolas", 11, "bold")


# ===========================================================
# Meshtastic GUI (no DB, no map)
# ===========================================================
class MeshtasticGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Packet Base")
        self.root.configure(bg=BG_MAIN)

        # state
        self.interface: SerialInterface | None = None
        self.connected = False
        self.current_com = tk.StringVar()
        self.selected_channel_index = tk.IntVar(value=0)

        self._ui_queue: queue.Queue = queue.Queue()

        self._build_style()
        self._build_ui()
        self._subscribe_events()
        self._refresh_ports()

        # pump events from worker threads to Tk
        self._schedule_ui_queue()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----------------- UI CONSTRUCTION -----------------
    def _build_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Cyber.TFrame", background=BG_MAIN)
        style.configure("Panel.TFrame", background=BG_PANEL)

        style.configure(
            "Header.TLabel",
            background=BG_HEADER,
            foreground=FG_TEXT,
            font=FONT_HEADER,
        )
        style.configure(
            "Cyber.TLabel",
            background=BG_MAIN,
            foreground=FG_TEXT,
            font=FONT_MAIN,
        )
        style.configure(
            "CyberSmall.TLabel",
            background=BG_MAIN,
            foreground=FG_MUTED,
            font=FONT_SMALL,
        )
        style.configure(
            "Cyber.TButton",
            background=ACCENT_SOFT,
            foreground=FG_TEXT,
            font=FONT_MAIN,
            borderwidth=0,
            padding=4,
        )
        style.map("Cyber.TButton", background=[("active", ACCENT)])

        style.configure(
            "Cyber.Treeview",
            background=BG_PANEL,
            foreground=FG_TEXT,
            fieldbackground=BG_PANEL,
            font=FONT_SMALL,
            rowheight=20,
        )
        style.configure(
            "Cyber.Treeview.Heading",
            background=BG_HEADER,
            foreground=ACCENT,
            font=("Consolas", 9, "bold"),
        )

    def _build_ui(self):
        # TOP BAR ------------------------------------------------
        top = ttk.Frame(self.root, style="Cyber.TFrame")
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        ttk.Label(top, text="COM Port:", style="Cyber.TLabel").pack(
            side=tk.LEFT, padx=(0, 4)
        )

        self.port_combo = ttk.Combobox(
            top, textvariable=self.current_com, width=15, state="readonly"
        )
        self.port_combo.pack(side=tk.LEFT)

        ttk.Button(
            top, text="Refresh", style="Cyber.TButton", command=self._refresh_ports
        ).pack(side=tk.LEFT, padx=(4, 4))

        self.btn_connect = ttk.Button(
            top, text="Connect", style="Cyber.TButton", command=self.on_connect_clicked
        )
        self.btn_connect.pack(side=tk.LEFT, padx=(4, 4))

        self.btn_disconnect = ttk.Button(
            top,
            text="Disconnect",
            style="Cyber.TButton",
            command=self.on_disconnect_clicked,
            state=tk.DISABLED,
        )
        self.btn_disconnect.pack(side=tk.LEFT, padx=(4, 12))

        self.lbl_status = ttk.Label(
            top, text="Disconnected", style="CyberSmall.TLabel"
        )
        self.lbl_status.pack(side=tk.LEFT, padx=(4, 10))

        # MAIN AREA ----------------------------------------------
        main = ttk.Frame(self.root, style="Cyber.TFrame")
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        # LEFT PANEL: serial log + detail
        left = ttk.Frame(main, style="Panel.TFrame")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        ttk.Label(
            left, text="Serial / Packet Feed", style="Header.TLabel", anchor="w"
        ).pack(side=tk.TOP, fill=tk.X)

        log_frame = ttk.Frame(left, style="Panel.TFrame")
        log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.txt_log = tk.Text(
            log_frame,
            bg="#050813",
            fg="#00ff8a",
            insertbackground="#00ff8a",
            relief=tk.FLAT,
            font=FONT_SMALL,
            wrap=tk.NONE,
        )
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll_y = ttk.Scrollbar(
            log_frame, command=self.txt_log.yview, orient=tk.VERTICAL
        )
        log_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.configure(yscrollcommand=log_scroll_y.set)

        # Detail view (raw JSON)
        detail_frame = ttk.Frame(left, style="Panel.TFrame")
        detail_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False)

        ttk.Label(
            detail_frame,
            text="Last Packet (JSON)",
            style="Header.TLabel",
            anchor="w",
        ).pack(side=tk.TOP, fill=tk.X)

        self.txt_detail = tk.Text(
            detail_frame,
            bg="#050813",
            fg=FG_TEXT,
            insertbackground=ACCENT,
            relief=tk.FLAT,
            height=10,
            font=("Consolas", 9),
            wrap=tk.NONE,
        )
        self.txt_detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll_y = ttk.Scrollbar(
            detail_frame, command=self.txt_detail.yview, orient=tk.VERTICAL
        )
        detail_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_detail.configure(yscrollcommand=detail_scroll_y.set)

        # RIGHT PANEL: nodes + messages
        right = ttk.Frame(main, style="Panel.TFrame")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False)

        nb = ttk.Notebook(right)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Nodes tab
        frame_nodes = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(frame_nodes, text="Nodes")

        self.tree_nodes = ttk.Treeview(
            frame_nodes,
            columns=("name", "id", "last_heard"),
            show="headings",
            style="Cyber.Treeview",
            selectmode="browse",
        )
        self.tree_nodes.heading("name", text="Name")
        self.tree_nodes.heading("id", text="ID")
        self.tree_nodes.heading("last_heard", text="Last Heard")
        self.tree_nodes.column("name", width=120, anchor="w")
        self.tree_nodes.column("id", width=110, anchor="center")
        self.tree_nodes.column("last_heard", width=110, anchor="center")
        self.tree_nodes.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        nodes_scroll = ttk.Scrollbar(
            frame_nodes, orient=tk.VERTICAL, command=self.tree_nodes.yview
        )
        nodes_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_nodes.configure(yscrollcommand=nodes_scroll.set)

        # Messages tab
        frame_messages = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(frame_messages, text="Messages")

        self.tree_messages = ttk.Treeview(
            frame_messages,
            columns=("time", "channel", "src", "dst", "text"),
            show="headings",
            style="Cyber.Treeview",
            selectmode="browse",
        )
        self.tree_messages.heading("time", text="Time")
        self.tree_messages.heading("channel", text="Ch")
        self.tree_messages.heading("src", text="From")
        self.tree_messages.heading("dst", text="To")
        self.tree_messages.heading("text", text="Text")
        self.tree_messages.column("time", width=80, anchor="center")
        self.tree_messages.column("channel", width=40, anchor="center")
        self.tree_messages.column("src", width=90, anchor="center")
        self.tree_messages.column("dst", width=90, anchor="center")
        self.tree_messages.column("text", width=260, anchor="w")
        self.tree_messages.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        msg_scroll = ttk.Scrollbar(
            frame_messages, orient=tk.VERTICAL, command=self.tree_messages.yview
        )
        msg_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_messages.configure(yscrollcommand=msg_scroll.set)

        # BOTTOM: send message ----------------------------------
        bottom = ttk.Frame(self.root, style="Cyber.TFrame")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 8))

        ttk.Label(bottom, text="Channel:", style="CyberSmall.TLabel").pack(
            side=tk.LEFT, padx=(0, 4)
        )
        self.entry_channel = tk.Spinbox(
            bottom,
            from_=0,
            to=7,
            width=3,
            textvariable=self.selected_channel_index,
            bg=BG_PANEL,
            fg=FG_TEXT,
            insertbackground=ACCENT,
            relief=tk.FLAT,
        )
        self.entry_channel.pack(side=tk.LEFT, padx=(0, 8))

        self.entry_msg = tk.Entry(
            bottom,
            bg="#050813",
            fg=FG_TEXT,
            insertbackground=ACCENT,
            relief=tk.FLAT,
            font=FONT_MAIN,
        )
        self.entry_msg.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.btn_send = ttk.Button(
            bottom,
            text="Send",
            style="Cyber.TButton",
            command=self.on_send_clicked,
            state=tk.DISABLED,
        )
        self.btn_send.pack(side=tk.RIGHT)

    # ----------------- PORT / CONNECTION -----------------
    def _refresh_ports(self):
        ports = list_ports.comports()
        names = [p.device for p in ports]
        self.port_combo["values"] = names
        if names and not self.current_com.get():
            self.current_com.set(names[0])

    def on_connect_clicked(self):
        if self.connected:
            return
        port = self.current_com.get().strip()
        if not port:
            messagebox.showerror("Port", "Select a COM port first.")
            return

        try:
            self._append_log(f"[SYS] Connecting to {port} ...")
            self.interface = SerialInterface(devPath=port)
            self.connected = True
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_disconnect.config(state=tk.NORMAL)
            self.btn_send.config(state=tk.NORMAL)
            self.lbl_status.configure(text=f"Connected: {port}", foreground=FG_OK)

            # trigger channel config fetch
            try:
                if self.interface.localNode:
                    self.interface.localNode.getURL()
            except Exception as e:
                self._append_log(f"[WARN] Could not fetch channel URL: {e}")

            self.root.after(1000, self._refresh_nodes_table)

        except Exception as e:
            self._append_log(f"[ERR] Connect failed: {e}")
            messagebox.showerror("Connect failed", str(e))
            self.interface = None
            self.connected = False

    def on_disconnect_clicked(self):
        self._disconnect()

    def _disconnect(self):
        if self.interface:
            try:
                self.interface.close()
            except Exception:
                pass
            self.interface = None

        self.connected = False
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_disconnect.config(state=tk.DISABLED)
        self.btn_send.config(state=tk.DISABLED)
        self.lbl_status.configure(text="Disconnected", foreground=FG_ERR)
        self._append_log("[SYS] Disconnected.")

    def on_close(self):
        self._disconnect()
        self.root.destroy()

    # ----------------- PUBSUB HOOKS -----------------
    def _subscribe_events(self):
        pub.subscribe(
            self._on_connection_established, "meshtastic.connection.established"
        )
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")

        # decoded packets
        pub.subscribe(self._on_receive, "meshtastic.receive")

        # raw serial / log lines from device
        pub.subscribe(self._on_log_line, "meshtastic.log.line")

    def _on_connection_established(self, interface=None, topic=pub.AUTO_TOPIC, **_):
        self._ui_queue.put(("connected", {"interface": interface}))

    def _on_connection_lost(self, interface=None, topic=pub.AUTO_TOPIC, **_):
        self._ui_queue.put(("disconnected", {}))

    def _on_receive(self, packet=None, interface=None, topic=pub.AUTO_TOPIC, **_):
        if packet is None:
            return
        self._ui_queue.put(("packet", {"packet": packet}))

    def _on_log_line(self, line=None, interface=None, topic=pub.AUTO_TOPIC, **_):
        if line is None:
            return
        self._ui_queue.put(("logline", {"line": line}))

    # ----------------- UI QUEUE PUMP -----------------
    def _schedule_ui_queue(self):
        self._process_ui_queue()
        self.root.after(100, self._schedule_ui_queue)

    def _process_ui_queue(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "connected":
                    self._on_ui_connected(payload.get("interface"))
                elif kind == "disconnected":
                    self._on_ui_disconnected()
                elif kind == "packet":
                    self._on_ui_packet(payload["packet"])
                elif kind == "logline":
                    self._on_ui_log_line(payload["line"])
        except queue.Empty:
            pass

    # ----------------- UI UPDATE HANDLERS -------------
    def _on_ui_connected(self, interface):
        self._append_log("[SYS] Meshtastic connection established.")
        self.lbl_status.configure(foreground=FG_OK)
        self._refresh_nodes_table()

    def _on_ui_disconnected(self):
        self._append_log("[SYS] Meshtastic connection lost.")
        self._disconnect()

    def _on_ui_packet(self, packet: dict):
        summary = self._format_packet_summary(packet)
        self._append_log(summary)

        pretty = json.dumps(packet, indent=2, default=str)
        self.txt_detail.delete("1.0", tk.END)
        self.txt_detail.insert(tk.END, pretty)

        self._refresh_nodes_table()
        self._maybe_add_message_row(packet)

    def _on_ui_log_line(self, line: str):
        # raw radio/router logs straight from the COM port; strip ANSI codes
        clean_line = ANSI_ESCAPE_RE.sub("", line)
        self._append_log(f"[LOG] {clean_line}")

    def _append_log(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}\n"
        self.txt_log.insert(tk.END, line)
        self.txt_log.see(tk.END)

    # ----------------- FORMAT PACKET SUMMARY ----------
    def _format_packet_summary(self, packet: dict) -> str:
        src = packet.get("fromId") or packet.get("from")
        dst = packet.get("toId") or packet.get("to")
        rx_rssi = packet.get("rxRssi")
        rx_snr = packet.get("rxSnr")
        channel = packet.get("channel", 0)

        decoded = packet.get("decoded", {}) or {}
        data = decoded.get("data") or {}
        pos = decoded.get("position") or {}

        text = decoded.get("text") or data.get("text")

        if text:
            msg_kind = "TEXT"
            preview = text
        elif pos:
            msg_kind = "POS"
            lat = pos.get("latitude")
            lon = pos.get("longitude")
            preview = f"lat={lat:.5f}, lon={lon:.5f}" if lat and lon else "position update"
        elif "telemetry" in decoded:
            msg_kind = "TELEM"
            preview = "telemetry"
        elif "user" in decoded:
            msg_kind = "USER"
            preview = "user info"
        else:
            msg_kind = "DATA"
            preview = "non-text payload"

        if isinstance(preview, str) and len(preview) > 70:
            preview = preview[:67] + "..."

        rssi_str = f"{rx_rssi} dBm" if isinstance(rx_rssi, (int, float)) else "?"
        snr_str = f"{rx_snr:.1f} dB" if isinstance(rx_snr, (int, float)) else "?"

        return (
            f"{msg_kind} ch{channel} {src} → {dst} "
            f"[RSSI {rssi_str}, SNR {snr_str}] :: {preview}"
        )

    # ----------------- NODES TABLE --------------------
    def _refresh_nodes_table(self):
        self.tree_nodes.delete(*self.tree_nodes.get_children())

        if not (self.interface and getattr(self.interface, "nodes", None)):
            return

        for node_key, node in self.interface.nodes.items():
            if isinstance(node_key, int):
                node_id_str = f"!{node_key:x}"
            else:
                node_id_str = str(node_key)

            user = getattr(node, "user", None) or getattr(
                node, "userInfo", None
            ) or {}
            if isinstance(user, dict):
                long_name = user.get("longName") or user.get("long_name") or ""
                short_name = user.get("shortName") or user.get("short_name") or ""
            else:
                long_name = getattr(user, "longName", "") if user else ""
                short_name = getattr(user, "shortName", "") if user else ""

            name = long_name or short_name or node_id_str

            last_heard = getattr(node, "lastHeard", None)
            if isinstance(last_heard, (int, float)) and last_heard > 0:
                dt_obj = datetime.fromtimestamp(last_heard)
                last_str = dt_obj.strftime("%m-%d %H:%M")
            else:
                last_str = "?"

            self.tree_nodes.insert(
                "",
                tk.END,
                values=(name, node_id_str, last_str),
            )

    # ----------------- MESSAGES TABLE -----------------
    def _maybe_add_message_row(self, packet: dict):
        decoded = packet.get("decoded", {}) or {}
        data = decoded.get("data") or {}
        text = decoded.get("text") or data.get("text")
        if not text:
            return

        src = packet.get("fromId") or packet.get("from")
        dst = packet.get("toId") or packet.get("to")
        channel = packet.get("channel", 0)
        ts = datetime.now().strftime("%H:%M:%S")

        display_text = text if len(text) <= 120 else text[:117] + "..."

        self.tree_messages.insert(
            "",
            tk.END,
            values=(ts, channel, src, dst, display_text),
        )

        max_rows = 500
        children = self.tree_messages.get_children()
        if len(children) > max_rows:
            self.tree_messages.delete(children[0])

    # ----------------- SEND MESSAGE -------------------
    def on_send_clicked(self):
        if not (self.interface and self.connected):
            messagebox.showerror("Not connected", "Connect to a node first.")
            return

        text = self.entry_msg.get().strip()
        if not text:
            return

        try:
            ch_index = int(self.selected_channel_index.get())
        except ValueError:
            ch_index = 0

        try:
            self.interface.sendText(
                text,
                destinationId=meshtastic.BROADCAST_ADDR,
                wantAck=True,
                channelIndex=ch_index,
            )
            self._append_log(f"[TX] ch{ch_index}: {text}")
            self.entry_msg.delete(0, tk.END)
        except Exception as e:
            self._append_log(f"[ERR] send failed: {e}")
            messagebox.showerror("Send failed", str(e))


# ===========================================================
# MAIN
# ===========================================================
def main():
    root = tk.Tk()
    MeshtasticGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

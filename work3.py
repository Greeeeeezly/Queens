#!/usr/bin/env python3
"""
Этап 3: N-ферзей с БЗ (стоимость + цвет) и приоритетами агентов.

Дополнительно к этапу 2:
1) Можно фиксировать позицию (строку) выбранного ферзя.
2) Фиксированные ферзи получают высший приоритет в цепочке взаимодействия.
3) Можно вручную задавать приоритет каждого агента.
4) Визуальное положение фигур на доске не меняется.

Агентная логика:
- каждый ферзь является отдельным потоком QueenAgent;
- порядок взаимодействия задается цепочкой сообщений;
- фиксированные агенты получают приоритет в цепочке;
- центральный класс не назначает состояния за агентов.
"""

from __future__ import annotations

import json
import ast
import queue
import random
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


DB_PATH = Path(__file__).with_name("queens_stage2.db")
MAX_SOLUTIONS = 500
COLORS = ("red", "green", "blue")

COLOR_BG = {
    "red": "#FADBD8",
    "green": "#D5F5E3",
    "blue": "#D6EAF8",
}

COLOR_FG = {
    "red": "#C0392B",
    "green": "#1E8449",
    "blue": "#1A5276",
}

COLOR_RU = {
    "red": "Красный",
    "green": "Зеленый",
    "blue": "Синий",
}


@dataclass(frozen=True)
class CellState:
    cost: float
    color: str


@dataclass
class AgentControl:
    agent_id: int
    priority: int
    fixed_row: Optional[int] = None
    required_color: Optional[str] = None


@dataclass
class SolutionRecord:
    positions: List[int]
    total_cost: float
    common_color: str
    interaction_order: List[int]


class QueenAgent(threading.Thread):
    """Независимый агент-ферзь с локальными состояниями и настройками управления."""

    def __init__(
        self,
        column: int,
        local_states: Dict[int, CellState],
        control: AgentControl,
        inbox: "queue.Queue[dict]",
        next_inbox: Optional["queue.Queue[dict]"],
        solutions: List["SolutionRecord"],
        solution_lock: threading.Lock,
        stop_flag: threading.Event,
        n: int,
        interaction_order: List[int],
    ) -> None:
        super().__init__(daemon=True)
        self.column = column
        self.local_states = local_states
        self.control = control
        self.inbox = inbox
        self.next_inbox = next_inbox
        self.solutions = solutions
        self.solution_lock = solution_lock
        self.stop_flag = stop_flag
        self.n = n
        self.interaction_order = interaction_order

    def get_state(self, row: int) -> CellState:
        return self.local_states[row]

    def candidate_rows(
        self,
        fixed_row: Optional[int],
        required_color: Optional[str],
        common_color: Optional[str],
    ) -> List[int]:
        if fixed_row is not None:
            rows = [fixed_row] if fixed_row in self.local_states else []
        else:
            rows = list(self.local_states.keys())

        out: List[int] = []
        for row in rows:
            state = self.local_states[row]
            if required_color is not None and state.color != required_color:
                continue
            if common_color is not None and state.color != common_color:
                continue
            out.append(row)
        return out

    def run(self) -> None:
        while not self.stop_flag.is_set():
            try:
                message = self.inbox.get(timeout=0.1)
            except queue.Empty:
                continue

            kind = message.get("kind")
            if kind == "STOP":
                return
            if kind == "DONE":
                if self.next_inbox is not None:
                    self.next_inbox.put({"kind": "DONE"})
                return
            if kind != "TRY":
                continue

            self._process_try(message)

    def _process_try(self, message: dict) -> None:
        positions: Dict[int, int] = dict(message["positions"])
        used_rows: set[int] = set(message["used_rows"])
        used_diag1: set[int] = set(message["used_diag1"])
        used_diag2: set[int] = set(message["used_diag2"])
        common_color: Optional[str] = message["common_color"]
        acc_cost = float(message["acc_cost"])

        rows = self.candidate_rows(
            fixed_row=self.control.fixed_row,
            required_color=self.control.required_color,
            common_color=common_color,
        )

        for row in rows:
            if self.stop_flag.is_set():
                return

            d1 = row - self.column
            d2 = row + self.column
            if row in used_rows or d1 in used_diag1 or d2 in used_diag2:
                continue

            state = self.get_state(row)
            new_positions = dict(positions)
            new_positions[self.column] = row
            new_color = state.color if common_color is None else common_color
            new_cost = acc_cost + state.cost

            if self.next_inbox is None:
                with self.solution_lock:
                    if len(self.solutions) < MAX_SOLUTIONS and len(new_positions) == self.n:
                        self.solutions.append(
                            SolutionRecord(
                                positions=[new_positions[i] for i in range(self.n)],
                                total_cost=round(new_cost, 2),
                                common_color=new_color,
                                interaction_order=self.interaction_order[:],
                            )
                        )
                    if len(self.solutions) >= MAX_SOLUTIONS:
                        self.stop_flag.set()
                continue

            self.next_inbox.put(
                {
                    "kind": "TRY",
                    "positions": new_positions,
                    "used_rows": used_rows | {row},
                    "used_diag1": used_diag1 | {d1},
                    "used_diag2": used_diag2 | {d2},
                    "common_color": new_color,
                    "acc_cost": new_cost,
                }
            )


class KnowledgeBase:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        self.conn.execute("PRAGMA busy_timeout=10000")
        self._init_db()

    def close(self) -> None:
        self.conn.close()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS queen_states (
                agent_id INTEGER NOT NULL,
                row_pos  INTEGER NOT NULL,
                cost     REAL NOT NULL,
                color    TEXT NOT NULL,
                PRIMARY KEY (agent_id, row_pos)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS solutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                n_queens INTEGER NOT NULL,
                positions TEXT NOT NULL,
                total_cost REAL NOT NULL,
                color TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()
        self._ensure_solutions_color_column()

    def _get_solutions_columns(self) -> set[str]:
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(solutions)")
        return {str(row[1]).lower() for row in cur.fetchall()}

    def _ensure_solutions_color_column(self) -> None:
        """
        Совместимость со старыми БД:
        - в старых версиях колонка называлась `colors`
        - в новых — `color`
        """
        cols = self._get_solutions_columns()
        if "color" in cols or "colors" in cols:
            return
        cur = self.conn.cursor()
        cur.execute("ALTER TABLE solutions ADD COLUMN color TEXT")
        self.conn.commit()

    def state_count(self, n: int) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM queen_states WHERE agent_id < ? AND row_pos < ?",
            (n, n),
        )
        return int(cur.fetchone()[0])

    def populate_random(self, n: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM queen_states WHERE agent_id < ? OR row_pos < ?", (n, n))

        seed = self._build_seed_solution(n)
        seed_color = random.choice(COLORS)

        for agent_id in range(n):
            for row_pos in range(n):
                cost = round(random.uniform(1.0, 15.0), 2)
                color = random.choice(COLORS)
                if row_pos == seed[agent_id]:
                    color = seed_color
                cur.execute(
                    """
                    INSERT OR REPLACE INTO queen_states(agent_id, row_pos, cost, color)
                    VALUES (?, ?, ?, ?)
                    """,
                    (agent_id, row_pos, cost, color),
                )
        self.conn.commit()

    @staticmethod
    def _build_seed_solution(n: int) -> List[int]:
        """Находит одно валидное классическое решение N-ферзей."""
        rows: List[Optional[int]] = [None] * n
        used_rows: set[int] = set()
        used_diag1: set[int] = set()
        used_diag2: set[int] = set()

        def bt(col: int) -> bool:
            if col == n:
                return True
            for row in range(n):
                d1 = row - col
                d2 = row + col
                if row in used_rows or d1 in used_diag1 or d2 in used_diag2:
                    continue
                rows[col] = row
                used_rows.add(row)
                used_diag1.add(d1)
                used_diag2.add(d2)
                if bt(col + 1):
                    return True
                rows[col] = None
                used_rows.remove(row)
                used_diag1.remove(d1)
                used_diag2.remove(d2)
            return False

        if not bt(0):
            raise RuntimeError(f"Не удалось построить seed-решение для N={n}")
        return [r for r in rows if r is not None]

    def ensure_states(self, n: int) -> None:
        if self.state_count(n) != n * n:
            self.populate_random(n)

    def load_state_map(self, n: int) -> Dict[Tuple[int, int], CellState]:
        self.ensure_states(n)
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT agent_id, row_pos, cost, color
            FROM queen_states
            WHERE agent_id < ? AND row_pos < ?
            ORDER BY agent_id, row_pos
            """,
            (n, n),
        )
        out: Dict[Tuple[int, int], CellState] = {}
        for a, r, c, color in cur.fetchall():
            norm_color = str(color).lower()
            if norm_color not in COLORS:
                norm_color = "red"
            out[(int(a), int(r))] = CellState(cost=float(c), color=norm_color)
        return out

    @staticmethod
    def _normalize_color(value: str) -> str:
        color = str(value).lower().strip()
        return color if color in COLORS else "red"

    @staticmethod
    def _parse_positions(raw: str) -> List[int]:
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                return [int(x) for x in value]
        except Exception:
            pass
        try:
            value = ast.literal_eval(raw)
            if isinstance(value, list):
                return [int(x) for x in value]
        except Exception:
            pass
        return []

    def load_solutions(self, n: int, ascending: bool = True) -> List["SolutionRecord"]:
        self._ensure_solutions_color_column()
        cols = self._get_solutions_columns()
        color_col = "color" if "color" in cols else "colors"
        order = "ASC" if ascending else "DESC"
        cur = self.conn.cursor()
        cur.execute(
            f"""
            SELECT positions, total_cost, {color_col}
            FROM solutions
            WHERE n_queens=?
            ORDER BY total_cost {order}, id ASC
            """,
            (n,),
        )
        out: List[SolutionRecord] = []
        for positions_raw, total_cost, color_raw in cur.fetchall():
            positions = self._parse_positions(str(positions_raw))
            raw = str(color_raw)
            if raw.startswith("["):
                try:
                    arr = ast.literal_eval(raw)
                    color = self._normalize_color(arr[0]) if isinstance(arr, list) and arr else "red"
                except Exception:
                    color = "red"
            else:
                color = self._normalize_color(raw)
            out.append(
                SolutionRecord(
                    positions=positions,
                    total_cost=float(total_cost),
                    common_color=color,
                    interaction_order=list(range(len(positions))),
                )
            )
        return out

    def update_cell(self, agent_id: int, row_pos: int, color: Optional[str] = None, cost: Optional[float] = None) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT cost, color FROM queen_states WHERE agent_id=? AND row_pos=?",
            (agent_id, row_pos),
        )
        row = cur.fetchone()
        if row is None:
            return
        old_cost, old_color = float(row[0]), str(row[1]).lower()
        new_color = (color or old_color).lower()
        if new_color not in COLORS:
            new_color = old_color
        new_cost = old_cost if cost is None else float(cost)
        cur.execute(
            """
            UPDATE queen_states
            SET cost=?, color=?
            WHERE agent_id=? AND row_pos=?
            """,
            (round(new_cost, 2), new_color, agent_id, row_pos),
        )
        self.conn.commit()

    def save_solutions(self, n: int, solutions: List[SolutionRecord]) -> bool:
        self._ensure_solutions_color_column()
        cols = self._get_solutions_columns()
        color_col = "color" if "color" in cols else "colors"

        try:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM solutions WHERE n_queens=?", (n,))
            sql = (
                f"INSERT INTO solutions(n_queens, positions, total_cost, {color_col}) "
                f"VALUES (?, ?, ?, ?)"
            )
            cur.executemany(
                sql,
                [
                    (n, json.dumps(s.positions, ensure_ascii=False), round(s.total_cost, 2), s.common_color)
                    for s in solutions
                ],
            )
            self.conn.commit()
            return True
        except sqlite3.OperationalError:
            return False


class NQueensSolverStage3:
    """
    Инфраструктурный запуск агентной цепочки с учетом приоритетов.

    Класс формирует порядок сообщений между агентами, но не выполняет
    централизованный выбор состояний.
    """

    def __init__(
        self,
        n: int,
        state_map: Dict[Tuple[int, int], CellState],
        controls: List[AgentControl],
    ) -> None:
        self.n = n
        self.controls = {c.agent_id: c for c in controls}
        self.interaction_order = self._build_interaction_order(controls)
        self._queues: List["queue.Queue[dict]"] = [queue.Queue() for _ in range(n)]
        self._queue_by_agent = {
            agent_id: self._queues[order_idx]
            for order_idx, agent_id in enumerate(self.interaction_order)
        }
        self._solution_lock = threading.Lock()
        self.solutions: List[SolutionRecord] = []
        self._stop = threading.Event()
        self.agents: List[QueenAgent] = []
        for order_idx, agent_id in enumerate(self.interaction_order):
            local_states: Dict[int, CellState] = {}
            for row in range(n):
                local_states[row] = state_map[(agent_id, row)]
            self.agents.append(
                QueenAgent(
                    column=agent_id,
                    local_states=local_states,
                    control=self.controls[agent_id],
                    inbox=self._queues[order_idx],
                    next_inbox=self._queues[order_idx + 1] if order_idx + 1 < n else None,
                    solutions=self.solutions,
                    solution_lock=self._solution_lock,
                    stop_flag=self._stop,
                    n=n,
                    interaction_order=self.interaction_order,
                )
            )

    @staticmethod
    def _build_interaction_order(controls: List[AgentControl]) -> List[int]:
        ordered = sorted(
            controls,
            key=lambda c: (0 if c.fixed_row is not None else 1, c.priority, c.agent_id),
        )
        return [c.agent_id for c in ordered]

    def stop(self) -> None:
        self._stop.set()
        for inbox in self._queues:
            inbox.put({"kind": "STOP"})

    def solve(self) -> None:
        self.solutions.clear()
        self._stop.clear()
        for agent in self.agents:
            agent.start()

        self._queues[0].put(
            {
                "kind": "TRY",
                "positions": {},
                "used_rows": set(),
                "used_diag1": set(),
                "used_diag2": set(),
                "common_color": None,
                "acc_cost": 0.0,
            }
        )
        self._queues[0].put({"kind": "DONE"})

        stop_sent = False
        while any(agent.is_alive() for agent in self.agents):
            if self._stop.is_set() and not stop_sent:
                for inbox in self._queues:
                    inbox.put({"kind": "STOP"})
                stop_sent = True
            for agent in self.agents:
                agent.join(timeout=0.05)


class QueensApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("N Ферзей — Этап 3: фиксация позиций и приоритеты")
        self.geometry("1320x780")
        self.minsize(1050, 640)

        self.kb = KnowledgeBase(DB_PATH)

        self.n_var = tk.IntVar(value=8)
        self.sort_asc_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Готово.")
        self.count_var = tk.StringVar(value="")
        self.info_var = tk.StringVar(value="")

        self.edit_color_var = tk.StringVar(value=COLORS[0])
        self.edit_cost_var = tk.StringVar(value="")
        self.fixed_row_var = tk.StringVar(value="-1")
        self.agent_color_var = tk.StringVar(value="any")
        self.priority_var = tk.StringVar(value="0")
        self.chain_var = tk.StringVar(value="Цепочка: -")

        self.solutions: List[SolutionRecord] = []
        self.state_map: Dict[Tuple[int, int], CellState] = {}
        self.agent_controls: Dict[int, AgentControl] = {}
        self.cur_solution_idx = 0
        self.selected_agent_id: Optional[int] = None
        self.selected_kb_cell: Optional[Tuple[int, int]] = None

        self._solver: Optional[NQueensSolverStage3] = None
        self._search_thread: Optional[threading.Thread] = None

        self._sync_agent_controls()
        self._build_ui()
        self._refresh_kb()
        self._refresh_agent_table()

    def _build_ui(self) -> None:
        self._build_top_bar()

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=44)
        paned.add(right, weight=56)
        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_top_bar(self) -> None:
        top = tk.Frame(self, bd=1, relief=tk.GROOVE, padx=8, pady=6, bg="#2C3E50")
        top.pack(fill=tk.X, side=tk.TOP)

        tk.Label(top, text="Количество ферзей N =", bg="#2C3E50", fg="white", font=("Arial", 10)).pack(side=tk.LEFT)
        tk.Spinbox(top, from_=4, to=14, width=4, textvariable=self.n_var, font=("Arial", 11, "bold")).pack(
            side=tk.LEFT, padx=(4, 10)
        )

        tk.Button(
            top,
            text="Обновить цвета/стоимости",
            command=self._populate_kb,
            bg="#2980B9",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=2,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            top,
            text="Загрузить из БД",
            command=self._load_solutions_from_db,
            bg="#8E44AD",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=2,
        ).pack(side=tk.LEFT, padx=4)

        self.solve_btn = tk.Button(
            top,
            text="Решить",
            command=self._solve,
            bg="#27AE60",
            fg="white",
            relief=tk.FLAT,
            font=("Arial", 10, "bold"),
            padx=12,
            pady=2,
        )
        self.solve_btn.pack(side=tk.LEFT, padx=4)

        self.stop_btn = tk.Button(
            top,
            text="Стоп",
            command=self._stop_search,
            bg="#C0392B",
            fg="white",
            relief=tk.FLAT,
            font=("Arial", 10, "bold"),
            padx=10,
            pady=2,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        tk.Label(top, text="   Сортировка:", bg="#2C3E50", fg="#BDC3C7").pack(side=tk.LEFT)
        tk.Radiobutton(
            top,
            text="↑ По возрастанию",
            variable=self.sort_asc_var,
            value=True,
            command=self._refresh_solutions_table,
            bg="#2C3E50",
            fg="white",
            selectcolor="#34495E",
            activebackground="#2C3E50",
        ).pack(side=tk.LEFT, padx=2)
        tk.Radiobutton(
            top,
            text="↓ По убыванию",
            variable=self.sort_asc_var,
            value=False,
            command=self._refresh_solutions_table,
            bg="#2C3E50",
            fg="white",
            selectcolor="#34495E",
            activebackground="#2C3E50",
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(top, textvariable=self.status_var, bg="#2C3E50", fg="#BDC3C7", font=("Arial", 9)).pack(
            side=tk.RIGHT, padx=8
        )

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        kb_tab = ttk.Frame(notebook)
        agent_tab = ttk.Frame(notebook)
        sol_tab = ttk.Frame(notebook)
        notebook.add(kb_tab, text="  База знаний  ")
        notebook.add(agent_tab, text="  Агенты и приоритеты  ")
        notebook.add(sol_tab, text="  Решения  ")

        self._build_kb_tab(kb_tab)
        self._build_agent_tab(agent_tab)
        self._build_solutions_tab(sol_tab)

    def _build_kb_tab(self, parent: ttk.Frame) -> None:
        tk.Label(
            parent,
            text="Клетка = (агент/столбец, строка). Для каждой клетки есть стоимость и цвет.",
            font=("Arial", 8),
            fg="#555",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=6, pady=(4, 0))

        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        cols = ("agent", "row", "cost", "color")
        self.kb_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.kb_tree.heading("agent", text="Агент")
        self.kb_tree.heading("row", text="Строка")
        self.kb_tree.heading("cost", text="Стоимость")
        self.kb_tree.heading("color", text="Цвет")
        self.kb_tree.column("agent", width=80, anchor=tk.CENTER, stretch=False)
        self.kb_tree.column("row", width=65, anchor=tk.CENTER, stretch=False)
        self.kb_tree.column("cost", width=85, anchor=tk.CENTER, stretch=False)
        self.kb_tree.column("color", width=100, anchor=tk.CENTER, stretch=False)

        for color in COLORS:
            self.kb_tree.tag_configure(color, background=COLOR_BG[color])

        sb_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.kb_tree.yview)
        self.kb_tree.configure(yscrollcommand=sb_y.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.kb_tree.pack(fill=tk.BOTH, expand=True)
        self.kb_tree.bind("<<TreeviewSelect>>", self._on_select_kb_cell)

        edit = tk.Frame(parent)
        edit.pack(fill=tk.X, padx=6, pady=(2, 4))
        self.edit_target_label = tk.StringVar(value="Выбрано: -")
        tk.Label(edit, textvariable=self.edit_target_label, font=("Arial", 9, "bold"), fg="#2C3E50").pack(
            side=tk.LEFT, padx=(0, 8)
        )

        tk.Label(edit, text="Цвет:", font=("Arial", 9)).pack(side=tk.LEFT)
        ttk.Combobox(edit, textvariable=self.edit_color_var, values=list(COLORS), state="readonly", width=8).pack(
            side=tk.LEFT, padx=4
        )

        tk.Label(edit, text="Стоимость:", font=("Arial", 9)).pack(side=tk.LEFT, padx=(8, 0))
        tk.Entry(edit, textvariable=self.edit_cost_var, width=8).pack(side=tk.LEFT, padx=4)

        tk.Button(
            edit,
            text="Применить",
            command=self._apply_cell_edit,
            bg="#7D3C98",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=1,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            edit,
            text="Фиксировать клетку агента",
            command=self._fix_selected_cell_for_agent,
            bg="#AF601A",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=1,
        ).pack(side=tk.LEFT, padx=2)

    def _build_agent_tab(self, parent: ttk.Frame) -> None:
        tk.Label(
            parent,
            text=(
                "Можно фиксировать КЛЕТКУ агента (строка+цвет), задавать цвет агента и приоритет. "
                "Фиксированные агенты всегда идут первыми в цепочке."
            ),
            font=("Arial", 8),
            fg="#555",
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=560,
        ).pack(fill=tk.X, padx=6, pady=(4, 0))

        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        cols = ("agent", "fixed_cell", "agent_color", "priority", "chain_pos", "mode")
        self.agent_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.agent_tree.heading("agent", text="Агент")
        self.agent_tree.heading("fixed_cell", text="Фикс. клетка")
        self.agent_tree.heading("agent_color", text="Цвет агента")
        self.agent_tree.heading("priority", text="Приоритет")
        self.agent_tree.heading("chain_pos", text="Позиция в цепочке")
        self.agent_tree.heading("mode", text="Режим")
        self.agent_tree.column("agent", width=70, anchor=tk.CENTER, stretch=False)
        self.agent_tree.column("fixed_cell", width=120, anchor=tk.CENTER, stretch=False)
        self.agent_tree.column("agent_color", width=95, anchor=tk.CENTER, stretch=False)
        self.agent_tree.column("priority", width=85, anchor=tk.CENTER, stretch=False)
        self.agent_tree.column("chain_pos", width=130, anchor=tk.CENTER, stretch=False)
        self.agent_tree.column("mode", width=80, anchor=tk.CENTER, stretch=False)
        self.agent_tree.tag_configure("fixed", background="#FCF3CF")

        sb_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.agent_tree.yview)
        self.agent_tree.configure(yscrollcommand=sb_y.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.agent_tree.pack(fill=tk.BOTH, expand=True)
        self.agent_tree.bind("<<TreeviewSelect>>", self._on_select_agent)

        controls = tk.Frame(parent)
        controls.pack(fill=tk.X, padx=6, pady=(2, 4))

        self.agent_target_var = tk.StringVar(value="Выбран агент: -")
        tk.Label(controls, textvariable=self.agent_target_var, font=("Arial", 9, "bold"), fg="#2C3E50").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        tk.Label(controls, text="Фикс. строка (-1=нет):", font=("Arial", 9)).pack(side=tk.LEFT)
        tk.Entry(controls, textvariable=self.fixed_row_var, width=5).pack(side=tk.LEFT, padx=4)
        tk.Label(controls, text="Цвет агента:", font=("Arial", 9)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Combobox(controls, textvariable=self.agent_color_var, values=("any",) + COLORS, state="readonly", width=7).pack(
            side=tk.LEFT, padx=4
        )
        tk.Label(controls, text="Приоритет:", font=("Arial", 9)).pack(side=tk.LEFT, padx=(8, 0))
        tk.Entry(controls, textvariable=self.priority_var, width=5).pack(side=tk.LEFT, padx=4)

        tk.Button(
            controls,
            text="Применить",
            command=self._apply_agent_settings,
            bg="#16A085",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=1,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            controls,
            text="Снять фиксацию",
            command=self._clear_agent_fix,
            bg="#A04000",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=1,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            controls,
            text="Сбросить приоритеты",
            command=self._reset_priorities,
            bg="#5D6D7E",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=1,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            controls,
            text="Снять все фиксации",
            command=self._clear_all_fixes,
            bg="#7B241C",
            fg="white",
            relief=tk.FLAT,
            padx=8,
            pady=1,
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(parent, textvariable=self.chain_var, font=("Consolas", 9), fg="#2C3E50", justify=tk.LEFT).pack(
            fill=tk.X, padx=6, pady=(0, 4)
        )

    def _build_solutions_tab(self, parent: ttk.Frame) -> None:
        tk.Label(
            parent,
            text="Допустимы только решения, где все ферзи на клетках одного цвета и не бьют друг друга.",
            font=("Arial", 8),
            fg="#555",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=6, pady=(4, 0))

        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        cols = ("num", "cost", "color", "positions")
        self.sol_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.sol_tree.heading("num", text="№")
        self.sol_tree.heading("cost", text="Стоимость")
        self.sol_tree.heading("color", text="Цвет")
        self.sol_tree.heading("positions", text="Позиции")
        self.sol_tree.column("num", width=45, anchor=tk.CENTER, stretch=False)
        self.sol_tree.column("cost", width=90, anchor=tk.CENTER, stretch=False)
        self.sol_tree.column("color", width=90, anchor=tk.CENTER, stretch=False)
        self.sol_tree.column("positions", width=220, anchor=tk.CENTER, stretch=False)

        sb_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.sol_tree.yview)
        self.sol_tree.configure(yscrollcommand=sb_y.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.sol_tree.pack(fill=tk.BOTH, expand=True)
        self.sol_tree.bind("<<TreeviewSelect>>", self._on_select_solution)

        tk.Label(parent, textvariable=self.count_var, font=("Arial", 9, "bold"), fg="#2C3E50", anchor=tk.W).pack(
            fill=tk.X, padx=6, pady=2
        )

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        tk.Label(parent, text="Доска и визуализация", font=("Arial", 11, "bold")).pack(pady=(6, 0))
        self.fig, self.ax = plt.subplots(figsize=(5.8, 5.8))
        self.fig.patch.set_facecolor("#F4F6F7")
        self.fig.subplots_adjust(left=0.05, right=0.95, top=0.93, bottom=0.05)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        nav = tk.Frame(parent)
        nav.pack(pady=(0, 3))
        tk.Button(nav, text="◀ Пред.", command=self._prev_solution, bg="#2980B9", fg="white", relief=tk.FLAT).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(nav, text="След. ▶", command=self._next_solution, bg="#2980B9", fg="white", relief=tk.FLAT).pack(
            side=tk.LEFT, padx=4
        )

        tk.Label(
            parent,
            textvariable=self.info_var,
            font=("Consolas", 9),
            fg="#2C3E50",
            wraplength=600,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=8, pady=(0, 4))

    def _sync_agent_controls(self) -> None:
        n = self.n_var.get()
        old = getattr(self, "agent_controls", {})
        new: Dict[int, AgentControl] = {}
        for agent_id in range(n):
            if agent_id in old:
                ctrl = old[agent_id]
                fixed = ctrl.fixed_row
                if fixed is not None and (fixed < 0 or fixed >= n):
                    fixed = None
                req_color = ctrl.required_color
                if req_color is not None and req_color not in COLORS:
                    req_color = None
                new[agent_id] = AgentControl(
                    agent_id=agent_id,
                    priority=ctrl.priority,
                    fixed_row=fixed,
                    required_color=req_color,
                )
            else:
                new[agent_id] = AgentControl(agent_id=agent_id, priority=agent_id, fixed_row=None)
        self.agent_controls = new

    def _interaction_order(self) -> List[int]:
        controls = list(self.agent_controls.values())
        controls.sort(key=lambda c: (0 if c.fixed_row is not None else 1, c.priority, c.agent_id))
        return [c.agent_id for c in controls]

    def _refresh_agent_table(self) -> None:
        self._sync_agent_controls()
        order = self._interaction_order()
        order_pos = {agent_id: i + 1 for i, agent_id in enumerate(order)}

        if not hasattr(self, "agent_tree"):
            return
        self.agent_tree.delete(*self.agent_tree.get_children())
        for agent_id in range(self.n_var.get()):
            ctrl = self.agent_controls[agent_id]
            if ctrl.fixed_row is None:
                fixed_text = "-"
            else:
                fixed_color = self.state_map.get((agent_id, ctrl.fixed_row))
                if fixed_color is None:
                    fixed_text = f"r{ctrl.fixed_row}"
                else:
                    fixed_text = f"r{ctrl.fixed_row}/{COLOR_RU.get(fixed_color.color, fixed_color.color)}"
            agent_color_text = "Любой" if ctrl.required_color is None else COLOR_RU.get(ctrl.required_color, ctrl.required_color)
            mode = "FIXED" if ctrl.fixed_row is not None else "FREE"
            tags = ("fixed",) if ctrl.fixed_row is not None else ()
            self.agent_tree.insert(
                "",
                tk.END,
                iid=str(agent_id),
                values=(agent_id, fixed_text, agent_color_text, ctrl.priority, order_pos[agent_id], mode),
                tags=tags,
            )

        chain = " -> ".join(
            (
                f"A{aid}*"
                if self.agent_controls[aid].fixed_row is not None
                else f"A{aid}"
            )
            for aid in order
        )
        self.chain_var.set(f"Цепочка: {chain}")

    def _on_select_agent(self, _event=None) -> None:
        sel = self.agent_tree.selection()
        if not sel:
            return
        agent_id = int(sel[0])
        self.selected_agent_id = agent_id
        ctrl = self.agent_controls[agent_id]
        self.agent_target_var.set(f"Выбран агент: {agent_id}")
        self.fixed_row_var.set(str(ctrl.fixed_row if ctrl.fixed_row is not None else -1))
        self.agent_color_var.set(ctrl.required_color if ctrl.required_color is not None else "any")
        self.priority_var.set(str(ctrl.priority))

    def _apply_agent_settings(self) -> None:
        if self.selected_agent_id is None:
            messagebox.showwarning("Настройка агента", "Сначала выберите агента.")
            return
        n = self.n_var.get()
        try:
            prio = int(self.priority_var.get().strip())
        except ValueError:
            messagebox.showerror("Ошибка", "Приоритет должен быть целым числом.")
            return
        if prio < 0:
            messagebox.showerror("Ошибка", "Приоритет должен быть >= 0.")
            return

        color_raw = self.agent_color_var.get().strip().lower()
        if color_raw == "any" or color_raw == "":
            required_color = None
        elif color_raw in COLORS:
            required_color = color_raw
        else:
            messagebox.showerror("Ошибка", "Цвет агента: any/red/green/blue.")
            return

        raw_row = self.fixed_row_var.get().strip()
        if raw_row in ("", "-1"):
            fixed_row = None
        else:
            try:
                fixed_row = int(raw_row)
            except ValueError:
                messagebox.showerror("Ошибка", "Фиксированная строка должна быть целым числом.")
                return
            if fixed_row < 0 or fixed_row >= n:
                messagebox.showerror("Ошибка", f"Строка должна быть от 0 до {n - 1}, либо -1.")
                return

        ctrl = self.agent_controls[self.selected_agent_id]
        ctrl.priority = prio
        ctrl.fixed_row = fixed_row
        ctrl.required_color = required_color
        self._refresh_agent_table()
        self.status_var.set(
            f"Агент {self.selected_agent_id}: fixed_row={fixed_row if fixed_row is not None else '-'}, "
            f"color={required_color if required_color is not None else 'any'}, priority={prio}."
        )

    def _clear_agent_fix(self) -> None:
        if self.selected_agent_id is None:
            messagebox.showwarning("Снятие фиксации", "Сначала выберите агента.")
            return
        self.agent_controls[self.selected_agent_id].fixed_row = None
        self.fixed_row_var.set("-1")
        self._refresh_agent_table()

    def _reset_priorities(self) -> None:
        for agent_id, ctrl in self.agent_controls.items():
            ctrl.priority = agent_id
        self._refresh_agent_table()

    def _clear_all_fixes(self) -> None:
        for ctrl in self.agent_controls.values():
            ctrl.fixed_row = None
        self._refresh_agent_table()

    def _validate_fixed_constraints(self) -> Tuple[bool, str]:
        fixed: List[Tuple[int, int, str]] = []
        n = self.n_var.get()
        for agent_id, ctrl in self.agent_controls.items():
            if ctrl.required_color is not None and ctrl.required_color not in COLORS:
                return False, f"У агента {agent_id} некорректный цвет: {ctrl.required_color}."
            if ctrl.fixed_row is None:
                continue
            row = ctrl.fixed_row
            if row < 0 or row >= n:
                return False, f"У агента {agent_id} некорректная фиксированная строка: {row}."
            color = self.state_map[(agent_id, row)].color
            if ctrl.required_color is not None and ctrl.required_color != color:
                return False, (
                    f"Агент {agent_id}: фиксированная клетка имеет цвет {COLOR_RU.get(color, color)}, "
                    f"но задан цвет агента {COLOR_RU.get(ctrl.required_color, ctrl.required_color)}."
                )
            fixed.append((agent_id, row, color))

        for i in range(len(fixed)):
            a1, r1, _ = fixed[i]
            for j in range(i + 1, len(fixed)):
                a2, r2, _ = fixed[j]
                if r1 == r2 or abs(a1 - a2) == abs(r1 - r2):
                    return False, f"Конфликт между фиксированными агентами {a1} и {a2}."

        color_set = {c for _, _, c in fixed}
        if len(color_set) > 1:
            ru = ", ".join(COLOR_RU.get(c, c) for c in sorted(color_set))
            return False, f"Фиксированные позиции имеют разные цвета ({ru}), решения невозможны."

        required_colors = {c.required_color for c in self.agent_controls.values() if c.required_color is not None}
        if len(required_colors) > 1:
            ru = ", ".join(COLOR_RU.get(c, c) for c in sorted(required_colors))
            return False, f"У агентов заданы разные цвета ({ru}), а по условию цвет решения должен быть один."
        return True, ""

    def _populate_kb(self) -> None:
        n = self.n_var.get()
        self.kb.populate_random(n)
        self._sync_agent_controls()
        self._refresh_kb()
        self._refresh_agent_table()
        self.solutions.clear()
        self.sol_tree.delete(*self.sol_tree.get_children())
        self.count_var.set("")
        self.info_var.set("")
        self._draw_board_only(n)
        self.status_var.set(f"База знаний обновлена для N={n}. Записей: {n*n}.")

    def _load_solutions_from_db(self) -> None:
        n = self.n_var.get()
        self._stop_search()
        loaded = self.kb.load_solutions(n=n, ascending=self.sort_asc_var.get())
        self.solutions = loaded
        self._refresh_solutions_table()
        if self.solutions:
            self.cur_solution_idx = 0
            self._select_solution_in_tree(0)
            self._draw_solution(0)
            self.status_var.set(f"Из БД загружено {len(self.solutions)} решений для N={n}.")
        else:
            self._draw_board_only(n)
            self.info_var.set("")
            self.status_var.set(f"В БД нет сохраненных решений для N={n}.")

    def _refresh_kb(self) -> None:
        n = self.n_var.get()
        self._sync_agent_controls()
        self.state_map = self.kb.load_state_map(n)
        self.kb_tree.delete(*self.kb_tree.get_children())
        for agent_id in range(n):
            for row in range(n):
                st = self.state_map[(agent_id, row)]
                self.kb_tree.insert(
                    "",
                    tk.END,
                    iid=f"{agent_id}:{row}",
                    values=(agent_id, row, f"{st.cost:.2f}", COLOR_RU[st.color]),
                    tags=(st.color,),
                )
        self._refresh_agent_table()
        self._draw_board_only(n)

    def _on_select_kb_cell(self, _event=None) -> None:
        sel = self.kb_tree.selection()
        if not sel:
            return
        agent_id, row = map(int, sel[0].split(":"))
        self.selected_kb_cell = (agent_id, row)
        st = self.state_map.get((agent_id, row))
        if st is None:
            return
        self.edit_target_label.set(f"Выбрано: агент={agent_id}, строка={row}")
        self.edit_color_var.set(st.color)
        self.edit_cost_var.set(f"{st.cost:.2f}")

    def _apply_cell_edit(self) -> None:
        sel = self.kb_tree.selection()
        if not sel:
            messagebox.showwarning("Изменение клетки", "Сначала выберите строку в таблице БЗ.")
            return

        agent_id, row = map(int, sel[0].split(":"))
        color = self.edit_color_var.get().lower().strip()
        if color not in COLORS:
            messagebox.showerror("Ошибка цвета", "Допустимые цвета: red, green, blue.")
            return

        cost_text = self.edit_cost_var.get().strip()
        if cost_text == "":
            cost = None
        else:
            try:
                cost = float(cost_text.replace(",", "."))
            except ValueError:
                messagebox.showerror("Ошибка стоимости", "Стоимость должна быть числом.")
                return
            if cost <= 0:
                messagebox.showerror("Ошибка стоимости", "Стоимость должна быть больше 0.")
                return

        self.kb.update_cell(agent_id=agent_id, row_pos=row, color=color, cost=cost)
        self._refresh_kb()
        self.status_var.set(f"Клетка ({agent_id}, {row}) обновлена: color={color}.")

    def _fix_selected_cell_for_agent(self) -> None:
        if self.selected_kb_cell is None:
            messagebox.showwarning("Фиксация клетки", "Сначала выберите клетку в таблице БЗ.")
            return
        agent_id, row = self.selected_kb_cell
        state = self.state_map.get((agent_id, row))
        if state is None:
            messagebox.showerror("Фиксация клетки", "Не удалось прочитать состояние выбранной клетки.")
            return
        ctrl = self.agent_controls[agent_id]
        ctrl.fixed_row = row
        ctrl.required_color = state.color
        self.selected_agent_id = agent_id
        self.agent_target_var.set(f"Выбран агент: {agent_id}")
        self.fixed_row_var.set(str(row))
        self.agent_color_var.set(state.color)
        self.priority_var.set(str(ctrl.priority))
        self._refresh_agent_table()
        self.status_var.set(
            f"Агент {agent_id} зафиксирован на клетке (row={row}, color={state.color})."
        )

    def _solve(self) -> None:
        self._stop_search()
        n = self.n_var.get()
        self._sync_agent_controls()
        self._refresh_kb()
        ok, msg = self._validate_fixed_constraints()
        if not ok:
            messagebox.showwarning("Проверка ограничений", msg)
            self.status_var.set("Поиск не запущен: исправьте фиксации.")
            return

        self.solve_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set(f"Поиск решений для N={n}...")
        self.solutions.clear()
        self.sol_tree.delete(*self.sol_tree.get_children())
        self.count_var.set("")
        self.info_var.set("")
        self.update_idletasks()

        self._solver = NQueensSolverStage3(
            n=n,
            state_map=self.state_map,
            controls=list(self.agent_controls.values()),
        )
        self._search_thread = threading.Thread(target=self._run_solver, args=(n,), daemon=True)
        self._search_thread.start()

    def _run_solver(self, n: int) -> None:
        if self._solver is None:
            return
        self._solver.solve()
        self.after(0, self._on_search_done, n)

    def _on_search_done(self, n: int) -> None:
        self.solve_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        if self._solver is None:
            return

        self.solutions = list(self._solver.solutions)
        self.solutions.sort(key=lambda s: s.total_cost, reverse=not self.sort_asc_var.get())
        saved_ok = self.kb.save_solutions(n, self.solutions)
        self._refresh_solutions_table()

        cnt = len(self.solutions)
        limit_note = " (достигнут лимит)" if self._solver._stop.is_set() and cnt >= MAX_SOLUTIONS else ""
        if saved_ok:
            self.status_var.set(f"Найдено {cnt} решений для N={n}{limit_note}.")
        else:
            self.status_var.set(
                f"Найдено {cnt} решений для N={n}{limit_note}, но БД занята: сохранение пропущено."
            )

        if cnt:
            self.cur_solution_idx = 0
            self._select_solution_in_tree(0)
            self._draw_solution(0)
        else:
            self._draw_board_only(n)
            self.info_var.set("Решений не найдено. Попробуйте изменить цвета клеток в БЗ.")

    def _refresh_solutions_table(self) -> None:
        self.solutions.sort(key=lambda s: s.total_cost, reverse=not self.sort_asc_var.get())
        self.sol_tree.delete(*self.sol_tree.get_children())
        for i, s in enumerate(self.solutions):
            self.sol_tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=(i + 1, f"{s.total_cost:.2f}", COLOR_RU.get(s.common_color, s.common_color), str(s.positions)),
            )
        order = "↑ по возрастанию" if self.sort_asc_var.get() else "↓ по убыванию"
        self.count_var.set(f"Всего решений: {len(self.solutions)} ({order})")

    def _on_select_solution(self, _event=None) -> None:
        sel = self.sol_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.cur_solution_idx = idx
        self._draw_solution(idx)

    def _prev_solution(self) -> None:
        if not self.solutions:
            return
        self.cur_solution_idx = (self.cur_solution_idx - 1) % len(self.solutions)
        self._select_solution_in_tree(self.cur_solution_idx)
        self._draw_solution(self.cur_solution_idx)

    def _next_solution(self) -> None:
        if not self.solutions:
            return
        self.cur_solution_idx = (self.cur_solution_idx + 1) % len(self.solutions)
        self._select_solution_in_tree(self.cur_solution_idx)
        self._draw_solution(self.cur_solution_idx)

    def _select_solution_in_tree(self, idx: int) -> None:
        iid = str(idx)
        self.sol_tree.selection_set(iid)
        self.sol_tree.focus(iid)
        self.sol_tree.see(iid)

    def _draw_board_only(self, n: int) -> None:
        self.ax.clear()
        for row in range(n):
            for col in range(n):
                st = self.state_map.get((col, row))
                face = COLOR_BG[st.color] if st else "#ECECEC"
                self.ax.add_patch(
                    patches.Rectangle(
                        (col, n - 1 - row),
                        1,
                        1,
                        linewidth=0.7,
                        edgecolor="#888",
                        facecolor=face,
                    )
                )
        self._finalize_board(n, f"Доска {n}x{n} (цвета клеток из БЗ)")
        self.canvas.draw()

    def _draw_solution(self, idx: int) -> None:
        s = self.solutions[idx]
        n = len(s.positions)
        self.ax.clear()

        for row in range(n):
            for col in range(n):
                st = self.state_map.get((col, row))
                face = COLOR_BG[st.color] if st else "#ECECEC"
                self.ax.add_patch(
                    patches.Rectangle(
                        (col, n - 1 - row),
                        1,
                        1,
                        linewidth=0.7,
                        edgecolor="#888",
                        facecolor=face,
                    )
                )

        for col, row in enumerate(s.positions):
            ctrl = self.agent_controls.get(col)
            fixed = ctrl is not None and ctrl.fixed_row is not None
            x, y = col + 0.5, n - 1 - row + 0.5
            self.ax.add_patch(patches.Circle((x + 0.05, y - 0.05), 0.36, color="#00000033", zorder=2))
            self.ax.add_patch(
                patches.Circle(
                    (x, y),
                    0.36,
                    facecolor=COLOR_FG.get(s.common_color, "#2C3E50"),
                    edgecolor="#1A252F",
                    linewidth=1.5,
                    zorder=3,
                )
            )
            if fixed:
                self.ax.add_patch(
                    patches.Circle((x, y), 0.42, fill=False, edgecolor="#F1C40F", linewidth=2.0, zorder=4)
                )
            self.ax.text(x, y, "♛", fontsize=16, ha="center", va="center", color="white", fontweight="bold", zorder=4)

        self._finalize_board(n, f"Решение №{idx + 1} | стоимость: {s.total_cost:.2f}")
        self.canvas.draw()

        fixed_desc = ", ".join(
            f"A{aid}=r{ctrl.fixed_row}"
            for aid, ctrl in sorted(self.agent_controls.items())
            if ctrl.fixed_row is not None
        )
        if fixed_desc == "":
            fixed_desc = "-"
        colors_desc = ", ".join(
            f"A{aid}={COLOR_RU.get(ctrl.required_color, ctrl.required_color)}"
            for aid, ctrl in sorted(self.agent_controls.items())
            if ctrl.required_color is not None
        )
        if colors_desc == "":
            colors_desc = "-"
        chain = " -> ".join(f"A{x}" for x in s.interaction_order)
        self.info_var.set(
            f"Позиции: {s.positions}\n"
            f"Общий цвет клеток: {COLOR_RU.get(s.common_color, s.common_color)}\n"
            f"Суммарная стоимость: {s.total_cost:.2f}\n"
            f"Фиксированные: {fixed_desc}\n"
            f"Цвета агентов: {colors_desc}\n"
            f"Цепочка взаимодействия: {chain}"
        )

    def _finalize_board(self, n: int, title: str) -> None:
        for k in range(n):
            self.ax.text(-0.32, n - 0.5 - k, str(k), ha="center", va="center", fontsize=8, color="#555")
            self.ax.text(k + 0.5, -0.32, str(k), ha="center", va="center", fontsize=8, color="#555")
        self.ax.set_xlim(-0.5, n)
        self.ax.set_ylim(-0.5, n)
        self.ax.set_aspect("equal")
        self.ax.axis("off")
        self.ax.set_title(title, fontsize=11, fontweight="bold", color="#2C3E50")

    def _stop_search(self) -> None:
        if self._solver is not None:
            self._solver.stop()
        if self._search_thread is not None and self._search_thread.is_alive():
            self._search_thread.join(timeout=1.0)
        self.solve_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

    def _on_close(self) -> None:
        self._stop_search()
        self.kb.close()
        plt.close("all")
        self.destroy()


def main() -> None:
    app = QueensApp()
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()


if __name__ == "__main__":
    main()

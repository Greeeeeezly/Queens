#!/usr/bin/env python3
"""
Этап 2: N-ферзей с БЗ (стоимость + цвет клетки).

Требования, реализованные в этом файле:
1) Для каждой клетки (column=row агента, row=позиция) хранится стоимость и цвет.
2) Клетки генерируются разными цветами (красный/зеленый/синий).
3) Цвет выбранной клетки можно менять вручную через UI.
4) Решение допустимо, если:
   - ферзи не бьют друг друга (классические правила N-ферзей),
   - все ферзи стоят на клетках одного и того же цвета.
5) Для каждого решения считается сумма стоимостей и доступна сортировка.

Агентная логика:
- каждый ферзь является отдельным потоком QueenAgent;
- у каждого агента есть собственная очередь входящих сообщений;
- агент самостоятельно фильтрует свои локальные состояния;
- итоговое решение получается через передачу сообщений между агентами.
"""

from __future__ import annotations

import json
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
class SolutionRecord:
    positions: List[int]
    total_cost: float
    common_color: str


class QueenAgent(threading.Thread):
    """Независимый агент-ферзь с локальными состояниями из базы знаний."""

    def __init__(
        self,
        column: int,
        local_states: Dict[int, CellState],
        inbox: "queue.Queue[dict]",
        next_inbox: Optional["queue.Queue[dict]"],
        solutions: List["SolutionRecord"],
        solution_lock: threading.Lock,
        stop_flag: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.column = column
        self.local_states = local_states
        self.inbox = inbox
        self.next_inbox = next_inbox
        self.solutions = solutions
        self.solution_lock = solution_lock
        self.stop_flag = stop_flag

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
        positions: List[int] = list(message["positions"])
        used_rows: set[int] = set(message["used_rows"])
        used_diag1: set[int] = set(message["used_diag1"])
        used_diag2: set[int] = set(message["used_diag2"])
        common_color: Optional[str] = message["common_color"]
        acc_cost = float(message["acc_cost"])

        for row, state in self.local_states.items():
            if self.stop_flag.is_set():
                return

            d1 = row - self.column
            d2 = row + self.column
            if row in used_rows or d1 in used_diag1 or d2 in used_diag2:
                continue
            if common_color is not None and state.color != common_color:
                continue

            new_positions = positions + [row]
            new_color = state.color if common_color is None else common_color
            new_cost = acc_cost + state.cost

            if self.next_inbox is None:
                with self.solution_lock:
                    if len(self.solutions) < MAX_SOLUTIONS:
                        self.solutions.append(
                            SolutionRecord(
                                positions=new_positions,
                                total_cost=round(new_cost, 2),
                                common_color=new_color,
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


class NQueensSolverStage2:
    """
    Инфраструктурный запуск агентной цепочки.

    Класс не выполняет централизованный перебор состояний, а создает очереди,
    запускает независимых агентов и отправляет первое сообщение.
    """

    def __init__(self, n: int, state_map: Dict[Tuple[int, int], CellState]) -> None:
        self.n = n
        self.state_map = state_map
        self._queues: List["queue.Queue[dict]"] = [queue.Queue() for _ in range(n)]
        self._solution_lock = threading.Lock()
        self.solutions: List[SolutionRecord] = []
        self._stop = threading.Event()
        self.agents: List[QueenAgent] = []
        for column in range(n):
            local_states = {row: state_map[(column, row)] for row in range(n)}
            self.agents.append(
                QueenAgent(
                    column=column,
                    local_states=local_states,
                    inbox=self._queues[column],
                    next_inbox=self._queues[column + 1] if column + 1 < n else None,
                    solutions=self.solutions,
                    solution_lock=self._solution_lock,
                    stop_flag=self._stop,
                )
            )

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
                "positions": [],
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
        self.title("N Ферзей — Этап 2 (цветные клетки + БЗ)")
        self.geometry("1250x760")
        self.minsize(980, 620)

        self.kb = KnowledgeBase(DB_PATH)

        self.n_var = tk.IntVar(value=8)
        self.sort_asc_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Готово.")
        self.count_var = tk.StringVar(value="")
        self.info_var = tk.StringVar(value="")

        self.edit_color_var = tk.StringVar(value=COLORS[0])
        self.edit_cost_var = tk.StringVar(value="")

        self.solutions: List[SolutionRecord] = []
        self.state_map: Dict[Tuple[int, int], CellState] = {}
        self.cur_solution_idx = 0

        self._solver: Optional[NQueensSolverStage2] = None
        self._search_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._refresh_kb()

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
        sol_tab = ttk.Frame(notebook)
        notebook.add(kb_tab, text="  База знаний  ")
        notebook.add(sol_tab, text="  Решения  ")

        self._build_kb_tab(kb_tab)
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

    def _populate_kb(self) -> None:
        n = self.n_var.get()
        self.kb.populate_random(n)
        self._refresh_kb()
        self.solutions.clear()
        self.sol_tree.delete(*self.sol_tree.get_children())
        self.count_var.set("")
        self.info_var.set("")
        self._draw_board_only(n)
        self.status_var.set(f"База знаний обновлена для N={n}. Записей: {n*n}.")

    def _refresh_kb(self) -> None:
        n = self.n_var.get()
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
        self._draw_board_only(n)

    def _on_select_kb_cell(self, _event=None) -> None:
        sel = self.kb_tree.selection()
        if not sel:
            return
        agent_id, row = map(int, sel[0].split(":"))
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

    def _solve(self) -> None:
        self._stop_search()
        n = self.n_var.get()
        self._refresh_kb()

        self.solve_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set(f"Поиск решений для N={n}...")
        self.solutions.clear()
        self.sol_tree.delete(*self.sol_tree.get_children())
        self.count_var.set("")
        self.info_var.set("")
        self.update_idletasks()

        self._solver = NQueensSolverStage2(n=n, state_map=self.state_map)
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
            x, y = col + 0.5, n - 1 - row + 0.5
            self.ax.add_patch(patches.Circle((x + 0.05, y - 0.05), 0.36, color="#00000033", zorder=2))
            self.ax.add_patch(patches.Circle((x, y), 0.36, facecolor="#2C3E50", edgecolor="#1A252F", linewidth=1.4, zorder=3))
            self.ax.text(x, y, "♛", fontsize=16, ha="center", va="center", color="white", fontweight="bold", zorder=4)

        self._finalize_board(n, f"Решение №{idx + 1} | стоимость: {s.total_cost:.2f}")
        self.canvas.draw()

        self.info_var.set(
            f"Позиции: {s.positions}\n"
            f"Общий цвет клеток: {COLOR_RU.get(s.common_color, s.common_color)}\n"
            f"Суммарная стоимость: {s.total_cost:.2f}"
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

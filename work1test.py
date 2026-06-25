#!/usr/bin/env python3
"""
Этап 1 — N Ферзей: агентная модель с поиском всех решений и GUI
================================================================
Задача о расстановке N ферзей на доске N×N так, чтобы ни одна пара
ферзей не атаковала друг друга (по горизонтали, вертикали, диагонали).

Функциональность:
  - Каждый ферзь — экземпляр отдельного класса QueenAgent
  - Каждый агент работает в собственном потоке и имеет очередь сообщений
  - Поиск всех допустимых расстановок выполняется через обмен сообщениями
  - Поиск выполняется в фоновом потоке — интерфейс не зависает
  - Кнопка «⏹ Стоп» для прерывания поиска
  - Лимит MAX_SOLUTIONS решений (защита от зависания)
  - Управление размером доски N (SpinBox, от 4 до 14)
  - Список решений с нумерацией; клик → визуализация на доске
  - Навигация кнопками «◀ Пред.» / «След. ▶»

Запуск: python queens_stage1.py
"""

import tkinter as tk
from tkinter import ttk
import queue
import threading
from typing import List, Optional

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ─────────────────────────── Константы ────────────────────────────────────

MAX_SOLUTIONS = 500   # максимум отображаемых решений

LIGHT = '#F0D9B5'
DARK  = '#B58863'


# ══════════════════════════ Агент-ферзь ════════════════════════════════════

class QueenAgent(threading.Thread):
    """
    Независимый агент-ферзь. Каждый экземпляр соответствует одному столбцу
    доски, имеет собственную очередь сообщений и самостоятельно расширяет
    полученную частичную конфигурацию допустимыми локальными состояниями.
    """

    def __init__(
        self,
        column: int,
        n: int,
        inbox: "queue.Queue[dict]",
        next_inbox: Optional["queue.Queue[dict]"],
        solutions: List[List[int]],
        solution_lock: threading.Lock,
        stop_flag: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.column: int = column
        self.n: int = n
        self.inbox = inbox
        self.next_inbox = next_inbox
        self.solutions = solutions
        self.solution_lock = solution_lock
        self.stop_flag = stop_flag

    def __repr__(self) -> str:
        return f"QueenAgent(col={self.column})"

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

        for row in range(self.n):
            if self.stop_flag.is_set():
                return

            d1 = row - self.column
            d2 = row + self.column
            if row in used_rows or d1 in used_diag1 or d2 in used_diag2:
                continue

            new_positions = positions + [row]
            if self.next_inbox is None:
                with self.solution_lock:
                    if len(self.solutions) < MAX_SOLUTIONS:
                        self.solutions.append(new_positions)
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
                }
            )


# ══════════════════════════ Решатель ═══════════════════════════════════════

class NQueensSolver:
    """
    Распределенный поиск всех расстановок N ферзей.
    Каждый ферзь — независимый агент со своей очередью сообщений.
    Этот класс не выбирает позиции за агентов, а только запускает цепочку
    сообщений и ожидает завершения работы.
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self._queues: List["queue.Queue[dict]"] = [queue.Queue() for _ in range(n)]
        self._solution_lock = threading.Lock()
        self.solutions: List[List[int]] = []
        self._stop_flag = threading.Event()
        self.agents: List[QueenAgent] = [
            QueenAgent(
                column=i,
                n=n,
                inbox=self._queues[i],
                next_inbox=self._queues[i + 1] if i + 1 < n else None,
                solutions=self.solutions,
                solution_lock=self._solution_lock,
                stop_flag=self._stop_flag,
            )
            for i in range(n)
        ]

    def stop(self) -> None:
        self._stop_flag.set()
        for inbox in self._queues:
            inbox.put({"kind": "STOP"})

    def solve(self) -> None:
        self.solutions.clear()
        self._stop_flag.clear()
        for agent in self.agents:
            agent.start()

        self._queues[0].put(
            {
                "kind": "TRY",
                "positions": [],
                "used_rows": set(),
                "used_diag1": set(),
                "used_diag2": set(),
            }
        )
        self._queues[0].put({"kind": "DONE"})

        stop_sent = False
        while any(agent.is_alive() for agent in self.agents):
            if self._stop_flag.is_set() and not stop_sent:
                for inbox in self._queues:
                    inbox.put({"kind": "STOP"})
                stop_sent = True
            for agent in self.agents:
                agent.join(timeout=0.05)


# ══════════════════════════ Главное окно ═══════════════════════════════════

class QueensApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("N Ферзей — Этап 1: Агентная модель")
        self.geometry("1100x700")
        self.minsize(900, 560)

        self.n_var    = tk.IntVar(value=8)
        self.solutions: List[List[int]] = []
        self._cur_idx = 0
        self._solver: Optional[NQueensSolver] = None
        self._search_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._draw_empty(self.n_var.get())

    # ─────────────────────────── UI ───────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_top_bar()
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        left  = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left,  weight=38)
        paned.add(right, weight=62)
        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_top_bar(self) -> None:
        top = tk.Frame(self, bd=1, relief=tk.GROOVE, padx=8, pady=6, bg='#2C3E50')
        top.pack(fill=tk.X, side=tk.TOP)

        tk.Label(top, text="Размер доски  N =",
                 bg='#2C3E50', fg='white', font=('Arial', 10)).pack(side=tk.LEFT)
        tk.Spinbox(top, from_=4, to=14, textvariable=self.n_var, width=4,
                   font=('Arial', 11, 'bold')).pack(side=tk.LEFT, padx=(2, 10))

        self._solve_btn = tk.Button(top, text="▶  Решить",
                  command=self._solve,
                  bg='#27AE60', fg='white', relief=tk.FLAT,
                  font=('Arial', 10, 'bold'), padx=12, pady=2)
        self._solve_btn.pack(side=tk.LEFT, padx=4)

        self._stop_btn = tk.Button(top, text="⏹  Стоп",
                  command=self._stop_search,
                  bg='#C0392B', fg='white', relief=tk.FLAT,
                  font=('Arial', 10, 'bold'), padx=12, pady=2,
                  state=tk.DISABLED)
        self._stop_btn.pack(side=tk.LEFT, padx=4)

        self._status = tk.StringVar(value="Введите N и нажмите «Решить».")
        tk.Label(top, textvariable=self._status,
                 bg='#2C3E50', fg='#BDC3C7', font=('Arial', 9)).pack(side=tk.RIGHT, padx=10)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        tk.Label(parent, text="Список решений",
                 font=('Arial', 10, 'bold')).pack(pady=(6, 0))
        tk.Label(parent,
                 text="Выберите решение для визуализации на доске.",
                 font=('Arial', 8), fg='#555', anchor=tk.W
                 ).pack(fill=tk.X, padx=6, pady=(2, 0))

        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        cols = ('#', 'positions')
        hdrs = ('№', 'Позиции ферзей (строка для каждого столбца)')
        self._sol_tree = ttk.Treeview(frame, columns=cols, show='headings')
        for c, h, w in zip(cols, hdrs, (46, 280)):
            self._sol_tree.heading(c, text=h, anchor=tk.CENTER)
            self._sol_tree.column(c, width=w, anchor=tk.CENTER, stretch=False)

        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._sol_tree.yview)
        self._sol_tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._sol_tree.pack(fill=tk.BOTH, expand=True)
        self._sol_tree.bind('<<TreeviewSelect>>', self._on_select_solution)

        self._sol_count = tk.StringVar(value="")
        tk.Label(parent, textvariable=self._sol_count,
                 font=('Arial', 9, 'bold'), fg='#2C3E50', anchor=tk.W
                 ).pack(fill=tk.X, padx=6, pady=(0, 4))

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        tk.Label(parent, text="Визуализация решения",
                 font=('Arial', 11, 'bold')).pack(pady=(6, 0))

        self._fig, self._ax = plt.subplots(figsize=(5.5, 5.5))
        self._fig.patch.set_facecolor('#F4F6F7')
        self._fig.subplots_adjust(left=0.05, right=0.95, top=0.93, bottom=0.05)

        self._canvas = FigureCanvasTkAgg(self._fig, master=parent)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Навигация
        nav = tk.Frame(parent)
        nav.pack(pady=(0, 4))
        tk.Button(nav, text="◀  Пред.",
                  command=self._prev_solution,
                  bg='#2980B9', fg='white', relief=tk.FLAT,
                  padx=10, pady=3).pack(side=tk.LEFT, padx=6)
        self._nav_label = tk.StringVar(value="")
        tk.Label(nav, textvariable=self._nav_label,
                 font=('Arial', 9), width=14, anchor=tk.CENTER).pack(side=tk.LEFT)
        tk.Button(nav, text="След.  ▶",
                  command=self._next_solution,
                  bg='#2980B9', fg='white', relief=tk.FLAT,
                  padx=10, pady=3).pack(side=tk.LEFT, padx=6)

        self._info_var = tk.StringVar(value="")
        tk.Label(parent, textvariable=self._info_var,
                 font=('Courier', 9), fg='#2C3E50',
                 wraplength=580, justify=tk.LEFT).pack(pady=(0, 4))

    # ─────────────────────────── поиск (в потоке) ─────────────────────────

    def _solve(self) -> None:
        self._stop_search()
        n = self.n_var.get()

        self._solve_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status.set(f"Поиск решений для N={n}…  (макс. {MAX_SOLUTIONS})")
        self.solutions = []
        self._cur_idx  = 0
        self._sol_tree.delete(*self._sol_tree.get_children())
        self._sol_count.set("")
        self._nav_label.set("")
        self._draw_empty(n)
        self.update_idletasks()

        self._solver = NQueensSolver(n)
        self._search_thread = threading.Thread(
            target=self._run_search,
            args=(n,),
            daemon=True,
        )
        self._search_thread.start()

    def _run_search(self, n: int) -> None:
        """Выполняется в фоновом потоке."""
        self._solver.solve()
        self.after(0, self._on_search_done, n)

    def _on_search_done(self, n: int) -> None:
        """Вызывается в главном потоке после завершения поиска."""
        self._solve_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)

        was_stopped = self._solver._stop_flag.is_set()
        self.solutions = self._solver.solutions

        # Заполнить список
        self._sol_tree.delete(*self._sol_tree.get_children())
        for i, pos in enumerate(self.solutions):
            self._sol_tree.insert('', tk.END, iid=str(i),
                                  values=(i + 1, str(pos)))

        count = len(self.solutions)
        suffix = f" (показаны первые {MAX_SOLUTIONS})" if was_stopped else ""
        self._sol_count.set(f"Всего решений: {count}{suffix}")
        self._status.set(f"Найдено {count} решений для N={n}{suffix}.")

        if self.solutions:
            self._cur_idx = 0
            self._sol_tree.selection_set('0')
            self._sol_tree.focus('0')
            self._sol_tree.see('0')
            self._nav_label.set(f"1 / {count}")
            self._draw_board(self.solutions[0], 1)
        else:
            self._info_var.set("Решений нет.")

    def _stop_search(self) -> None:
        if self._solver is not None:
            self._solver.stop()
        if self._search_thread is not None and self._search_thread.is_alive():
            self._search_thread.join(timeout=1.0)
        self._solve_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)

    # ─────────────────────────── навигация ────────────────────────────────

    def _on_select_solution(self, _event=None) -> None:
        sel = self._sol_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self._cur_idx = idx
        self._nav_label.set(f"{idx + 1} / {len(self.solutions)}")
        self._draw_board(self.solutions[idx], idx + 1)

    def _prev_solution(self) -> None:
        if not self.solutions:
            return
        self._jump_to((self._cur_idx - 1) % len(self.solutions))

    def _next_solution(self) -> None:
        if not self.solutions:
            return
        self._jump_to((self._cur_idx + 1) % len(self.solutions))

    def _jump_to(self, idx: int) -> None:
        self._cur_idx = idx
        iid = str(idx)
        self._sol_tree.selection_set(iid)
        self._sol_tree.focus(iid)
        self._sol_tree.see(iid)
        self._nav_label.set(f"{idx + 1} / {len(self.solutions)}")
        self._draw_board(self.solutions[idx], idx + 1)

    # ─────────────────────────── рисование ───────────────────────────────

    def _draw_empty(self, n: int) -> None:
        self._ax.clear()
        for i in range(n):
            for j in range(n):
                fc = LIGHT if (i + j) % 2 == 0 else DARK
                self._ax.add_patch(patches.Rectangle(
                    (j, n - 1 - i), 1, 1,
                    linewidth=0.5, edgecolor='#888', facecolor=fc))
        self._finalize_board(n, f"Доска {n}×{n}  —  нажмите «Решить»")
        self._info_var.set("")

    def _draw_board(self, positions: List[int], sol_num: int) -> None:
        n = len(positions)
        self._ax.clear()

        # Клетки
        for i in range(n):
            for j in range(n):
                fc = LIGHT if (i + j) % 2 == 0 else DARK
                self._ax.add_patch(patches.Rectangle(
                    (j, n - 1 - i), 1, 1,
                    linewidth=0.5, edgecolor='#888', facecolor=fc))

        # Ферзи
        for col, row in enumerate(positions):
            x, y = col + 0.5, n - 1 - row + 0.5
            self._ax.add_patch(patches.Circle(
                (x + 0.05, y - 0.05), 0.36, color='#00000033', zorder=2))
            self._ax.add_patch(patches.Circle(
                (x, y), 0.36,
                facecolor='#2C3E50', edgecolor='#1A252F',
                linewidth=1.5, zorder=3))
            self._ax.text(x, y, '♛', fontsize=16,
                          ha='center', va='center',
                          color='white', fontweight='bold', zorder=4)

        self._finalize_board(n, f"Решение №{sol_num}")
        self._info_var.set(
            f"Позиции (строка для каждого столбца): {positions}"
        )

    def _finalize_board(self, n: int, title: str) -> None:
        for k in range(n):
            self._ax.text(-0.32, n - 0.5 - k, str(k),
                          ha='center', va='center', fontsize=8, color='#555')
            self._ax.text(k + 0.5, -0.32, str(k),
                          ha='center', va='center', fontsize=8, color='#555')
        self._ax.set_xlim(-0.5, n)
        self._ax.set_ylim(-0.5, n)
        self._ax.set_aspect('equal')
        self._ax.axis('off')
        self._ax.set_title(title, fontsize=11, fontweight='bold', color='#2C3E50')
        self._canvas.draw()

    # ─────────────────────────── завершение ───────────────────────────────

    def _on_close(self) -> None:
        self._stop_search()
        plt.close('all')
        self.destroy()


# ══════════════════════════ Точка входа ════════════════════════════════════

def main() -> None:
    app = QueensApp()
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()


if __name__ == '__main__':
    main()

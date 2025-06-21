#!/usr/bin/env python3

import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path

# Path to the summary file
summary_file = Path.home() / "adsb-analytics" / "summaries" / "today.txt"

def show_summary(text: str):
    root = tk.Tk()
    root.title("✈️ Daily Air Traffic Summary")
    root.geometry("600x400+300+100")  # width x height + x_offset + y_offset
    root.attributes("-topmost", True)

    # Scrollable text area
    scroll_text = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Arial", 12))
    scroll_text.insert(tk.END, text)
    scroll_text.configure(state='disabled')  # Make read-only
    scroll_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # Close button
    close_button = tk.Button(root, text="Close", command=root.destroy)
    close_button.pack(pady=5)

    root.mainloop()

if summary_file.exists():
    with open(summary_file, "r") as f:
        show_summary(f.read())
else:
    print("[INFO] No summary file found.")


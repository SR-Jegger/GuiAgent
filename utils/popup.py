"""
Step popup - Blocking dialog with countdown timer.
"""


class StepPopup:
    """Topmost popup window for displaying step information."""

    @staticmethod
    def show_blocking(
        title,
        text,
        image_path=None,
        timeout_sec=5,
        width=960,
        height=540,
        pos=None,
        image_ratio=0.55,
    ):
        """
        Show a blocking, always-on-top popup with an image on top
        and scrollable text below.
        """
        import tkinter as tk
        from PIL import ImageTk

        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        root.resizable(False, False)

        if pos is None:
            root.update_idletasks()
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            x = int((sw - width) / 2)
            y = int(sh * 0.12)
        else:
            x, y = pos
        root.geometry(f"{width}x{height}+{x}+{y}")

        frm = tk.Frame(root, bg="#1f1f1f")
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        lbl_title = tk.Label(
            frm, text=title, bg="#1f1f1f", fg="#ffffff",
            font=("Segoe UI", 12, "bold"), anchor="w",
        )
        lbl_title.pack(fill="x", pady=(0, 6))

        content_h = height - 90
        image_h = max(80, int(content_h * image_ratio))
        text_h = max(60, content_h - image_h)

        image_frame = tk.Frame(frm, bg="#1f1f1f", height=image_h)
        image_frame.pack(fill="x")
        image_frame.pack_propagate(False)

        img_label = tk.Label(image_frame, bg="#1f1f1f")
        img_label.pack(fill="both", expand=True)
        photo_ref = {"img": None}

        def render_image():
            if not image_path:
                img_label.config(text="(No image)", fg="#bbbbbb")
                return
            try:
                from PIL import Image
                with Image.open(image_path) as im_src:
                    img = im_src.convert("RGB")
                avail_w = width - 24
                avail_h = image_h - 10
                iw, ih = img.size
                ratio = min(avail_w / iw, avail_h / ih)
                new_w = max(1, int(iw * ratio))
                new_h = max(1, int(ih * ratio))
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img_resized)
                img_label.config(image=photo)
                photo_ref["img"] = photo
            except Exception as e:
                img_label.config(text=f"Image load failed: {e}", fg="#ff6666")

        render_image()

        text_frame = tk.Frame(frm, bg="#1f1f1f", height=text_h)
        text_frame.pack(fill="both", expand=True, pady=(6, 0))
        text_frame.pack_propagate(False)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")
        txt = tk.Text(
            text_frame, wrap="word", bg="#262626", fg="#e8e8e8",
            insertbackground="#e8e8e8", relief="flat",
        )
        txt.pack(side="left", fill="both", expand=True)
        txt.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=txt.yview)
        txt.insert("1.0", text or "")
        txt.config(state="disabled")

        bottom = tk.Frame(frm, bg="#1f1f1f")
        bottom.pack(fill="x", pady=(6, 0))
        countdown_var = tk.StringVar()

        def close():
            try:
                root.destroy()
            except Exception:
                pass

        def on_key(event):
            if event.keysym in ("Escape", "Return"):
                close()

        root.bind("<Escape>", on_key)
        root.bind("<Return>", on_key)

        lbl_count = tk.Label(
            bottom, textvariable=countdown_var,
            bg="#1f1f1f", fg="#bbbbbb", font=("Segoe UI", 10),
        )
        lbl_count.pack(side="left")

        btn = tk.Button(bottom, text="Close", command=close)
        btn.pack(side="right")

        remaining = [timeout_sec]

        def tick():
            remaining[0] -= 1
            if remaining[0] <= 0:
                close()
            else:
                countdown_var.set(
                    f"Auto-close in {remaining[0]}s (Esc/Enter to dismiss)"
                )
                root.after(1000, tick)

        countdown_var.set(
            f"Auto-close in {timeout_sec}s (Esc/Enter to dismiss)"
        )
        root.after(1000, tick)
        root.mainloop()

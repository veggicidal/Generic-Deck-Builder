import io
import asyncio
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageTk
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


APP_NAME = "Generic TCG Deckbuilder"
DATA_VERSION = 2
OCR_ENGINE_NAME = "Windows.Media.Ocr"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def get_app_base_path():
    """Return the folder that owns bundled resources in source and exe modes."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_path(relative_path):
    return os.path.join(get_app_base_path(), relative_path)


def get_file_signature(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


class ImageText:
    """Handles image preprocessing and text extraction."""

    _ocr_bindings = None
    _ocr_engine = None

    @classmethod
    def load_ocr_bindings(cls):
        if cls._ocr_bindings:
            return cls._ocr_bindings
        try:
            from winsdk.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
            from winsdk.windows.media.ocr import OcrEngine
            from winsdk.windows.storage.streams import DataWriter
        except ImportError:
            try:
                from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
                from winrt.windows.media.ocr import OcrEngine
                from winrt.windows.storage.streams import DataWriter
            except ImportError as exc:
                raise RuntimeError(
                    "Windows OCR requires the winsdk Python package. "
                    "Install it with: python -m pip install winsdk"
                ) from exc
        cls._ocr_bindings = {
            "BitmapPixelFormat": BitmapPixelFormat,
            "SoftwareBitmap": SoftwareBitmap,
            "OcrEngine": OcrEngine,
            "DataWriter": DataWriter,
        }
        return cls._ocr_bindings

    @classmethod
    def get_ocr_engine(cls):
        if cls._ocr_engine:
            return cls._ocr_engine
        bindings = cls.load_ocr_bindings()
        engine = bindings["OcrEngine"].try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError(
                "Windows OCR is not available for the current user profile languages. "
                "Install a Windows OCR language pack in Settings > Time & language > "
                "Language & region, then try again."
            )
        cls._ocr_engine = engine
        return engine

    @staticmethod
    def preprocess_image(image_path, settings, rotation=0):
        try:
            image = Image.open(image_path)
            image.load()
        except Exception as exc:
            raise ValueError(f"Failed to load image at {image_path}") from exc

        if rotation:
            image = image.rotate(-rotation, expand=True)

        image = ImageOps.grayscale(image)
        width = max(int(image.width * settings["scale_percent"] / 100), 1)
        height = max(int(image.height * settings["scale_percent"] / 100), 1)
        if (width, height) != image.size:
            image = image.resize((width, height), Image.Resampling.LANCZOS)

        blur_radius = max(int(settings["smoothing_percent"]) / 100, 0)
        if blur_radius:
            image = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        image = ImageOps.autocontrast(image)
        contrast_factor = max(0.5, min(2.5, int(settings["contrast_percent"]) / 100))
        image = ImageEnhance.Contrast(image).enhance(contrast_factor)
        sharpness_factor = max(0.0, min(3.0, int(settings["sharpness_percent"]) / 100))
        image = ImageEnhance.Sharpness(image).enhance(sharpness_factor)
        return image.convert("RGBA")

    @staticmethod
    def image_to_software_bitmap(image, engine, bindings):
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        max_dimension = getattr(engine, "max_image_dimension", 0) or 0
        if max_dimension and max(image.size) > max_dimension:
            scale = max_dimension / max(image.size)
            new_size = (
                max(int(image.width * scale), 1),
                max(int(image.height * scale), 1),
            )
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        SoftwareBitmap = bindings["SoftwareBitmap"]
        BitmapPixelFormat = bindings["BitmapPixelFormat"]
        DataWriter = bindings["DataWriter"]

        bitmap = SoftwareBitmap(BitmapPixelFormat.RGBA8, image.width, image.height)
        writer = DataWriter()
        writer.write_bytes(image.tobytes())
        bitmap.copy_from_buffer(writer.detach_buffer())
        return bitmap

    @classmethod
    async def extract_text_async(cls, image):
        engine = cls.get_ocr_engine()
        bindings = cls.load_ocr_bindings()
        bitmap = cls.image_to_software_bitmap(image, engine, bindings)
        try:
            result = await engine.recognize_async(bitmap)
            return str(getattr(result, "text", "") or "").strip()
        finally:
            close_bitmap = getattr(bitmap, "close", None)
            if close_bitmap:
                close_bitmap()

    @classmethod
    def extract_text(cls, image):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(cls.extract_text_async(image))
        raise RuntimeError(
            "Windows OCR cannot be run synchronously while another asyncio loop is active."
        )


class FileManager:
    """Handles app file formats and legacy text import."""

    @staticmethod
    def load_image_files(directory):
        files = []
        for root, _, filenames in os.walk(directory):
            for filename in filenames:
                if filename.lower().endswith(IMAGE_EXTENSIONS):
                    files.append(os.path.join(root, filename))
        return sorted(files, key=lambda path: os.path.basename(path).lower())

    @staticmethod
    def save_collection(collection, card_text, filepath, card_rotation, ocr_cache):
        data = {
            "version": DATA_VERSION,
            "type": "collection",
            "collection": collection,
            "card_text": card_text,
            "card_rotation": card_rotation,
            "ocr_cache": ocr_cache,
        }
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

    @staticmethod
    def load_collection(filepath):
        with open(filepath, "r", encoding="utf-8") as file:
            first_char = file.read(1)
            file.seek(0)
            if first_char == "{":
                data = json.load(file)
                return (
                    data.get("collection", []),
                    data.get("card_text", {}),
                    {
                        path: int(rotation)
                        for path, rotation in data.get("card_rotation", {}).items()
                    },
                    data.get("ocr_cache", {}),
                )
            return FileManager.load_legacy_collection(file)

    @staticmethod
    def load_legacy_collection(file):
        collection = []
        card_text = {}
        card_rotation = {}
        for line in file:
            parts = line.strip().split("|")
            if len(parts) >= 2:
                card_path = parts[0]
                collection.append(card_path)
                card_text[card_path] = parts[1].replace("@", "\n")
                card_rotation[card_path] = int(parts[2]) if len(parts) > 2 else 0
        return collection, card_text, card_rotation, {}

    @staticmethod
    def save_deck(filepath, subdecks, card_rotation):
        data = {
            "version": DATA_VERSION,
            "type": "deck",
            "subdecks": {
                name: {"cards": info.get("cards", {})}
                for name, info in subdecks.items()
            },
            "card_rotation": card_rotation,
        }
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

    @staticmethod
    def load_deck(filepath):
        with open(filepath, "r", encoding="utf-8") as file:
            first_char = file.read(1)
            file.seek(0)
            if first_char == "{":
                data = json.load(file)
                rotations = {
                    path: int(rotation)
                    for path, rotation in data.get("card_rotation", {}).items()
                }
                subdecks = {}
                for name, info in data.get("subdecks", {}).items():
                    subdecks[name] = {"cards": info.get("cards", {})}
                return subdecks, rotations
            return FileManager.load_legacy_deck(file)

    @staticmethod
    def load_legacy_deck(file):
        subdecks = {}
        card_rotation = {}
        current_subdeck = None
        for line in file:
            line = line.strip()
            if not line:
                continue
            if line.startswith("Subdeck:"):
                current_subdeck = line.split(":", 1)[-1].strip()
                subdecks[current_subdeck] = {"cards": {}}
                continue
            if current_subdeck:
                path_count = line.rsplit(" x", 1)
                if len(path_count) != 2:
                    continue
                path = path_count[0]
                count_rot = path_count[1].strip().split(" r")
                count = int(count_rot[0])
                rotation = int(count_rot[1]) if len(count_rot) > 1 else 0
                card_name = os.path.basename(path)
                subdecks[current_subdeck]["cards"][card_name] = {
                    "path": path,
                    "count": count,
                }
                card_rotation[path] = rotation
        return subdecks, card_rotation


class PDFManager:
    """Handles the creation of PDFs from card images."""

    def __init__(self, images, output_path, options):
        self.images = images
        self.output_path = output_path
        self.options = options

    def create_pdf(self):
        processed_images = self.prepare_images()
        c = canvas.Canvas(
            self.output_path,
            pagesize=landscape(letter)
            if self.options["orientation"] == "landscape"
            else letter,
        )
        self.layout_images_on_pdf(c, processed_images)
        c.save()

    def prepare_images(self):
        processed_images = []
        for img in self.images:
            cropped_img = img.crop(
                (
                    self.options["crop"],
                    self.options["crop"],
                    img.width - self.options["crop"],
                    img.height - self.options["crop"],
                )
            )
            extended_img = Image.new(
                "RGB",
                (
                    cropped_img.width + 2 * self.options["extend_h"],
                    cropped_img.height + 2 * self.options["extend_v"],
                ),
                (0, 0, 0),
            )
            extended_img.paste(
                cropped_img, (self.options["extend_h"], self.options["extend_v"])
            )
            processed_images.append(extended_img)
        return processed_images

    def layout_images_on_pdf(self, canvas_obj, processed_images):
        image_width = self.options["image_width_inch"] * 72
        image_height = self.options["image_height_inch"] * 72
        num_columns = max(
            int(
                (
                    canvas_obj._pagesize[0]
                    - 2 * self.options["margin"]
                    + self.options["h_spacing"]
                )
                // (image_width + self.options["h_spacing"])
            ),
            1,
        )
        num_rows = max(
            int(
                (
                    canvas_obj._pagesize[1]
                    - 2 * self.options["margin"]
                    + self.options["v_spacing"]
                )
                // (image_height + self.options["v_spacing"])
            ),
            1,
        )

        x_start = (
            canvas_obj._pagesize[0]
            - (
                num_columns * image_width
                + self.options["h_spacing"] * (num_columns - 1)
            )
        ) / 2
        y_start = (
            canvas_obj._pagesize[1]
            - 2 * image_height
            + image_height * num_rows
            + self.options["v_spacing"] * (num_rows - 1)
        ) / 2

        curr_count = 0
        for img in processed_images:
            if curr_count > 0 and curr_count % (num_columns * num_rows) == 0:
                canvas_obj.showPage()
            col = curr_count % num_columns
            row = curr_count // num_columns % num_rows
            img_stream = io.BytesIO()
            img.convert("RGB").save(img_stream, format="PNG")
            img_stream.seek(0)
            canvas_obj.drawImage(
                ImageReader(img_stream),
                x_start + col * (image_width + self.options["h_spacing"]),
                y_start - row * (image_height + self.options["v_spacing"]),
                image_width,
                image_height,
            )
            curr_count += 1


class CardApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.collection = []
        self.subdecks = {}
        self.current_card = None
        self.card_text = {}
        self.card_rotation = {}
        self.ocr_cache = {}
        self.thumbnail_cache = {}
        self.ocr_errors = []
        self.ocr_queue = queue.Queue()
        self.ocr_cancel_event = threading.Event()
        self.ocr_thread = None
        self.active_ocr_settings = None
        self.pending_collection_refresh = False
        self.collection_root_dir = None
        self.current_collection_dir = None
        self.collection_view_items = []

        self.scale_percent = 200
        self.smoothing_percent = 30
        self.contrast_percent = 125
        self.sharpness_percent = 125

        self.root.geometry("1200x800")
        self.root.resizable(True, True)
        self.setup_ui()
        self.new_deck()

    def get_ocr_settings(self):
        return {
            "ocr_engine": OCR_ENGINE_NAME,
            "scale_percent": int(self.scale_percent),
            "smoothing_percent": int(self.smoothing_percent),
            "contrast_percent": int(self.contrast_percent),
            "sharpness_percent": int(self.sharpness_percent),
        }

    def setup_ui(self):
        self.setup_menu()
        self.setup_main_pane()

    def setup_menu(self):
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="New Deck", command=self.new_deck)
        file_menu.add_command(label="Save Deck", command=self.save_deck)
        file_menu.add_command(label="Open Deck", command=self.open_deck)
        file_menu.add_separator()
        file_menu.add_command(label="Create PDF", command=self.create_pdf)
        file_menu.add_separator()
        file_menu.add_command(label="Save Collection File", command=self.save_collection)
        file_menu.add_command(label="Load Collection File", command=self.load_collection_file)
        file_menu.add_separator()
        file_menu.add_command(
            label="Adjust Windows OCR Processing", command=self.open_slider_window
        )
        file_menu.add_command(label="Show OCR Error Log", command=self.show_ocr_errors)
        menu.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menu)

    def setup_main_pane(self):
        top_frame = tk.Frame(self.root)
        top_frame.pack(pady=5, fill=tk.X)

        tk.Label(top_frame, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.update_collection_view)
        self.search_entry = tk.Entry(top_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.load_button = tk.Button(
            top_frame, text="Load Card Images", command=self.load_cards
        )
        self.load_button.pack(side=tk.LEFT)

        self.ocr_button = tk.Button(
            top_frame, text="Make Image Text Searchable", command=self.load_card_text
        )
        self.ocr_button.pack(side=tk.LEFT)

        self.cancel_ocr_button = tk.Button(
            top_frame, text="Cancel OCR", command=self.cancel_ocr, state=tk.DISABLED
        )
        self.cancel_ocr_button.pack(side=tk.LEFT)

        status_frame = tk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=4)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            status_frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(status_frame, textvariable=self.status_var, width=34, anchor=tk.W).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        main_paned_window = tk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned_window.pack(fill=tk.BOTH, expand=True)
        collection_frame, deck_frame, card_preview = self.setup_collection_and_deck_widgets(
            main_paned_window
        )
        main_paned_window.add(collection_frame)
        main_paned_window.add(card_preview)
        main_paned_window.add(deck_frame)

        preview_container = tk.Frame(card_preview)
        preview_container.pack(fill=tk.BOTH, expand=True)
        tk.Button(
            preview_container, text="Rotate 90 degrees", command=self.rotate_current_card
        ).pack(side=tk.BOTTOM, fill=tk.X)

        self.card_preview = tk.Label(
            preview_container,
            text="Select a card to preview",
            bg="gray",
            width=80,
            height=20,
        )
        self.card_preview.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        textbox_frame = tk.Frame(preview_container)
        textbox_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, padx=5, pady=5)
        self.textbox = tk.Text(textbox_frame, wrap=tk.WORD, height=8)
        textbox_scrollbar = tk.Scrollbar(textbox_frame, orient=tk.VERTICAL)
        self.textbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        textbox_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.textbox.config(yscrollcommand=textbox_scrollbar.set)
        textbox_scrollbar.config(command=self.textbox.yview)
        self.textbox.bind("<KeyRelease>", self.update_card_text)

    def setup_collection_and_deck_widgets(self, paned_window):
        collection_frame = tk.Frame(paned_window)
        tk.Label(collection_frame, text="Collection:", width=40).pack(anchor=tk.W)
        collection_list_frame = tk.Frame(collection_frame)
        collection_list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.collection_listbox = tk.Listbox(collection_list_frame)
        collection_scrollbar = tk.Scrollbar(collection_list_frame, orient=tk.VERTICAL)
        self.collection_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        collection_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.collection_listbox.config(yscrollcommand=collection_scrollbar.set)
        collection_scrollbar.config(command=self.collection_listbox.yview)
        self.collection_listbox.bind("<<ListboxSelect>>", self.display_card)
        self.collection_listbox.bind("<Double-Button-1>", self.open_collection_item)
        tk.Button(
            collection_frame, text="Add to Deck", command=self.add_card_to_deck
        ).pack(fill=tk.X)
        card_preview = tk.Frame(paned_window)
        deck_frame = self.setup_deck_section(paned_window)
        return collection_frame, deck_frame, card_preview

    def setup_deck_section(self, paned_window):
        deck_frame = tk.PanedWindow(paned_window, orient=tk.VERTICAL)
        self.tab_control = ttk.Notebook(deck_frame)
        self.tab_control.pack(fill=tk.BOTH, expand=True)
        tk.Button(deck_frame, text="Add Subdeck", command=self.add_new_subdeck).pack(
            side=tk.BOTTOM, fill=tk.X
        )
        return deck_frame

    def open_slider_window(self):
        slider_window = tk.Toplevel(self.root)
        slider_window.title("Adjust Windows OCR Processing")
        slider_window.resizable(False, False)

        controls_frame = tk.Frame(slider_window, padx=10, pady=10)
        controls_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(controls_frame, text="Image Scale:").pack(anchor=tk.W)
        scale_percent_slider = tk.Scale(
            controls_frame,
            from_=100,
            to_=300,
            orient=tk.HORIZONTAL,
            length=280,
            label="Percent",
        )
        scale_percent_slider.set(self.scale_percent)
        scale_percent_slider.pack(fill=tk.X)

        tk.Label(controls_frame, text="Smoothing:").pack(anchor=tk.W)
        smoothing_slider = tk.Scale(
            controls_frame,
            from_=0,
            to_=100,
            orient=tk.HORIZONTAL,
            length=280,
            label="Percent",
        )
        smoothing_slider.set(self.smoothing_percent)
        smoothing_slider.pack(fill=tk.X)

        tk.Label(controls_frame, text="Contrast:").pack(anchor=tk.W)
        contrast_slider = tk.Scale(
            controls_frame,
            from_=50,
            to_=250,
            orient=tk.HORIZONTAL,
            length=280,
            label="Percent",
        )
        contrast_slider.set(self.contrast_percent)
        contrast_slider.pack(fill=tk.X)

        tk.Label(controls_frame, text="Sharpness:").pack(anchor=tk.W)
        sharpness_slider = tk.Scale(
            controls_frame,
            from_=0,
            to_=300,
            orient=tk.HORIZONTAL,
            length=280,
            label="Percent",
        )
        sharpness_slider.set(self.sharpness_percent)
        sharpness_slider.pack(fill=tk.X)

        result_label = tk.Label(
            controls_frame, text="", anchor=tk.W, justify=tk.LEFT, wraplength=280
        )
        result_label.pack(fill=tk.X, pady=(8, 0))

        def selected_settings():
            return {
                "ocr_engine": OCR_ENGINE_NAME,
                "scale_percent": scale_percent_slider.get(),
                "smoothing_percent": smoothing_slider.get(),
                "contrast_percent": contrast_slider.get(),
                "sharpness_percent": sharpness_slider.get(),
            }

        def apply_changes():
            self.scale_percent = scale_percent_slider.get()
            self.smoothing_percent = smoothing_slider.get()
            self.contrast_percent = contrast_slider.get()
            self.sharpness_percent = sharpness_slider.get()
            messagebox.showinfo(
                "Parameters Applied",
                "Windows OCR processing parameters updated successfully.",
            )

        def update_ocr_text():
            if not self.current_card:
                messagebox.showinfo("No Card Selected", "Select a card before testing OCR.")
                return
            try:
                result_label.config(text="Testing current card...")
                slider_window.update_idletasks()
                settings = selected_settings()
                rotation = self.card_rotation.get(self.current_card, 0)
                processed_image = ImageText.preprocess_image(
                    self.current_card, settings, rotation
                )
                text = ImageText.extract_text(processed_image).strip()
                self.set_card_text(self.current_card, text, settings)
                self.textbox.delete("1.0", tk.END)
                self.textbox.insert("1.0", text)
                self.update_collection_view()
                result_label.config(
                    text=f"Found {len(text)} characters on the current card."
                )
            except Exception as exc:
                result_label.config(text="OCR test failed.")
                messagebox.showerror(
                    "Windows OCR Error", f"Failed to process and extract text: {exc}"
                )

        button_frame = tk.Frame(controls_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Button(button_frame, text="Apply", command=apply_changes).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        tk.Button(
            button_frame, text="Test Current Card", command=update_ocr_text
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def show_ocr_errors(self):
        log_window = tk.Toplevel(self.root)
        log_window.title("OCR Error Log")
        text = tk.Text(log_window, wrap=tk.WORD, width=100, height=30)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(
            "1.0",
            "\n\n".join(self.ocr_errors) if self.ocr_errors else "No OCR errors recorded.",
        )
        text.config(state=tk.DISABLED)

    def rotate_current_card(self):
        if not self.current_card:
            return
        current_rotation = self.card_rotation.get(self.current_card, 0)
        self.card_rotation[self.current_card] = (current_rotation + 90) % 360
        self.preview_current_card()

    def update_card_text(self, event=None):
        if self.current_card:
            self.set_card_text(self.current_card, self.textbox.get("1.0", tk.END).strip())

    def set_card_text(self, card_path, text, settings=None):
        self.card_text[card_path] = text
        signature = get_file_signature(card_path)
        if signature:
            self.ocr_cache[card_path] = {
                "text": text,
                "rotation": self.card_rotation.get(card_path, 0),
                "signature": signature,
                "settings": settings or self.get_ocr_settings(),
            }

    def cache_entry_is_valid(self, card_path, settings):
        entry = self.ocr_cache.get(card_path)
        return (
            entry
            and entry.get("signature") == get_file_signature(card_path)
            and entry.get("rotation") == self.card_rotation.get(card_path, 0)
            and entry.get("settings") == settings
        )

    def load_card_text(self):
        if self.ocr_thread and self.ocr_thread.is_alive():
            messagebox.showinfo("OCR Running", "OCR is already running.")
            return
        if not self.collection:
            messagebox.showinfo("No Cards", "Load card images before running OCR.")
            return
        if not messagebox.askokcancel(
            "Load Card Text",
            "Windows OCR can take a while. Cached cards will be skipped. Do you want to proceed?",
        ):
            return

        settings = self.get_ocr_settings()
        self.active_ocr_settings = settings
        self.ocr_errors.clear()
        self.ocr_cancel_event.clear()
        self.pending_collection_refresh = False
        self.progress_var.set(0)
        self.status_var.set("Starting Windows OCR...")
        self.ocr_button.config(state=tk.DISABLED)
        self.cancel_ocr_button.config(state=tk.NORMAL)
        self.ocr_thread = threading.Thread(
            target=self.ocr_worker, args=(list(self.collection), settings), daemon=True
        )
        self.ocr_thread.start()
        self.root.after(100, self.poll_ocr_queue)

    def ocr_worker(self, cards, settings):
        total = len(cards)
        for index, card_path in enumerate(cards, start=1):
            if self.ocr_cancel_event.is_set():
                self.ocr_queue.put(("cancelled", index - 1, total))
                return
            try:
                if self.cache_entry_is_valid(card_path, settings):
                    text = self.ocr_cache[card_path].get("text", "")
                    self.ocr_queue.put(("text", card_path, text, index, total, True))
                    continue
                rotation = self.card_rotation.get(card_path, 0)
                processed_image = ImageText.preprocess_image(card_path, settings, rotation)
                text = ImageText.extract_text(processed_image)
                text = text.replace("|", "").replace("@", "").strip()
                self.ocr_queue.put(("text", card_path, text, index, total, False))
            except Exception as exc:
                self.ocr_queue.put(("error", card_path, str(exc), index, total))
        self.ocr_queue.put(("done", total, total))

    def poll_ocr_queue(self):
        should_continue = True
        while True:
            try:
                message = self.ocr_queue.get_nowait()
            except queue.Empty:
                break

            kind = message[0]
            if kind == "text":
                _, card_path, text, index, total, from_cache = message
                self.set_card_text(card_path, text, self.active_ocr_settings)
                self.pending_collection_refresh = True
                source = "cached" if from_cache else "OCR"
                self.status_var.set(f"{source}: {index}/{total}")
                self.progress_var.set((index / total) * 100)
            elif kind == "error":
                _, card_path, error, index, total = message
                self.ocr_errors.append(f"{card_path}\n{error}")
                self.status_var.set(f"OCR error: {index}/{total}")
                self.progress_var.set((index / total) * 100)
            elif kind == "cancelled":
                _, index, total = message
                self.status_var.set(f"OCR cancelled at {index}/{total}")
                should_continue = False
                self.finish_ocr(cancelled=True)
            elif kind == "done":
                _, index, total = message
                self.progress_var.set(100)
                self.status_var.set(f"OCR complete: {index}/{total}")
                should_continue = False
                self.finish_ocr(cancelled=False)

        if should_continue and self.ocr_thread and self.ocr_thread.is_alive():
            self.root.after(100, self.poll_ocr_queue)

    def cancel_ocr(self):
        self.ocr_cancel_event.set()
        self.status_var.set("Cancelling OCR...")

    def finish_ocr(self, cancelled):
        self.ocr_button.config(state=tk.NORMAL)
        self.cancel_ocr_button.config(state=tk.DISABLED)
        self.active_ocr_settings = None
        if self.pending_collection_refresh:
            self.update_collection_view()
            self.pending_collection_refresh = False
        if self.current_card:
            self.textbox.delete("1.0", tk.END)
            self.textbox.insert("1.0", self.card_text.get(self.current_card, ""))
        if self.ocr_errors:
            messagebox.showwarning(
                "OCR Finished With Errors",
                f"{len(self.ocr_errors)} image(s) could not be processed. "
                "Use File > Show OCR Error Log for details.",
            )
        elif not cancelled:
            messagebox.showinfo("Success", "Card text loaded.")

    def save_collection(self):
        collection_file = filedialog.asksaveasfilename(
            defaultextension=".gdbcollection",
            filetypes=[
                ("GDB collection files", "*.gdbcollection"),
                ("JSON files", "*.json"),
                ("Text files", "*.txt"),
            ],
        )
        if collection_file:
            FileManager.save_collection(
                self.collection,
                self.card_text,
                collection_file,
                self.card_rotation,
                self.ocr_cache,
            )
            messagebox.showinfo("Success", "Collection saved successfully.")

    def load_collection_file(self):
        collection_file = filedialog.askopenfilename(
            filetypes=[
                ("Collection files", "*.gdbcollection *.json *.txt"),
                ("All files", "*.*"),
            ]
        )
        if collection_file:
            (
                self.collection,
                self.card_text,
                self.card_rotation,
                self.ocr_cache,
            ) = FileManager.load_collection(collection_file)
            self.set_collection_root_from_cards()
            self.thumbnail_cache.clear()
            self.update_collection_view()
            self.status_var.set(f"Loaded {len(self.collection)} cards")

    def add_subdeck_tab(self, subdeck_name):
        tab_frame = tk.Frame(self.tab_control)
        self.tab_control.add(tab_frame, text=subdeck_name)
        deck_list_frame = tk.Frame(tab_frame)
        deck_list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        deck_listbox = tk.Listbox(deck_list_frame)
        deck_scrollbar = tk.Scrollbar(deck_list_frame, orient=tk.VERTICAL)
        deck_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        deck_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        deck_listbox.config(yscrollcommand=deck_scrollbar.set)
        deck_scrollbar.config(command=deck_listbox.yview)
        deck_listbox.bind("<<ListboxSelect>>", self.display_card_from_deck)
        self.subdecks[subdeck_name] = {
            "frame": tab_frame,
            "listbox": deck_listbox,
            "cards": self.subdecks.get(subdeck_name, {}).get("cards", {}),
        }
        button_frame = tk.Frame(tab_frame)
        button_frame.pack(fill=tk.X)
        tk.Button(
            button_frame,
            text="Remove Card from Deck",
            command=lambda name=subdeck_name: self.remove_card_from_deck(name),
        ).pack(side=tk.LEFT, fill=tk.X)
        tk.Button(
            button_frame,
            text="Rename Subdeck",
            command=lambda name=subdeck_name: self.rename_subdeck(name),
        ).pack(side=tk.LEFT, fill=tk.X)
        tk.Button(
            button_frame,
            text="Delete Subdeck",
            command=lambda name=subdeck_name: self.delete_subdeck(name),
        ).pack(side=tk.LEFT, fill=tk.X)
        self.subdecks[subdeck_name]["count_label"] = tk.Label(
            tab_frame, text="Total cards: 0"
        )
        self.subdecks[subdeck_name]["count_label"].pack(side=tk.BOTTOM, fill=tk.X)
        self.update_deck_view(subdeck_name)

    def add_new_subdeck(self):
        next_number = len(self.subdecks) + 1
        new_subdeck_name = f"Subdeck {next_number}"
        while new_subdeck_name in self.subdecks:
            next_number += 1
            new_subdeck_name = f"Subdeck {next_number}"
        self.subdecks[new_subdeck_name] = {"cards": {}}
        self.add_subdeck_tab(new_subdeck_name)

    def rename_subdeck(self, subdeck_name):
        new_name = simpledialog.askstring("Rename Subdeck", "Enter new name:")
        if new_name and new_name not in self.subdecks:
            self.subdecks[new_name] = self.subdecks.pop(subdeck_name)
            saved_subdecks = {
                name: {"cards": info.get("cards", {})}
                for name, info in self.subdecks.items()
            }
            for tab_id in list(self.tab_control.tabs()):
                self.tab_control.forget(tab_id)
            self.subdecks = saved_subdecks
            for name in list(self.subdecks.keys()):
                self.add_subdeck_tab(name)

    def delete_subdeck(self, subdeck_name):
        if len(self.subdecks) > 1 and subdeck_name in self.subdecks:
            tab_frame = self.subdecks[subdeck_name]["listbox"].master
            tab_index = self.tab_control.index(tab_frame)
            self.tab_control.forget(tab_index)
            del self.subdecks[subdeck_name]

    def load_cards(self):
        directory = filedialog.askdirectory(title="Select Card Folder")
        if directory:
            self.collection_root_dir = os.path.abspath(directory)
            self.current_collection_dir = self.collection_root_dir
            self.collection = FileManager.load_image_files(directory)
            self.thumbnail_cache.clear()
            self.update_collection_view()
            self.status_var.set(f"Loaded {len(self.collection)} cards")

    def set_collection_root_from_cards(self):
        existing_cards = [path for path in self.collection if os.path.exists(path)]
        if not existing_cards:
            self.collection_root_dir = None
            self.current_collection_dir = None
            return
        try:
            self.collection_root_dir = os.path.commonpath(
                [os.path.dirname(path) for path in existing_cards]
            )
        except ValueError:
            self.collection_root_dir = os.path.dirname(existing_cards[0])
        self.current_collection_dir = self.collection_root_dir

    def directory_has_cards(self, directory):
        directory = os.path.abspath(directory)
        for card_path in self.collection:
            try:
                if os.path.commonpath([directory, os.path.abspath(card_path)]) == directory:
                    return True
            except ValueError:
                continue
        return False

    def get_current_folder_cards(self):
        if not self.current_collection_dir:
            return list(self.collection)
        current = os.path.abspath(self.current_collection_dir)
        cards = []
        for card_path in self.collection:
            if os.path.dirname(os.path.abspath(card_path)) == current:
                cards.append(card_path)
        return sorted(cards, key=lambda path: os.path.basename(path).lower())

    def get_current_child_folders(self):
        if not self.current_collection_dir or not os.path.isdir(self.current_collection_dir):
            return []
        folders = []
        for entry in os.scandir(self.current_collection_dir):
            if entry.is_dir() and self.directory_has_cards(entry.path):
                folders.append(entry.path)
        return sorted(folders, key=lambda path: os.path.basename(path).lower())

    def format_card_label(self, card_path, include_relative_path=False):
        if include_relative_path and self.collection_root_dir:
            try:
                return os.path.relpath(card_path, self.collection_root_dir)
            except ValueError:
                pass
        return os.path.basename(card_path)

    def update_collection_view(self, *_):
        search_query = self.search_var.get().lower()
        self.collection_listbox.delete(0, tk.END)
        self.collection_view_items = []

        if search_query:
            for card in self.collection:
                card_label = self.format_card_label(card, include_relative_path=True)
                extracted_text = self.card_text.get(card, "")
                if search_query in card_label.lower() or search_query in extracted_text.lower():
                    self.collection_listbox.insert(tk.END, card_label)
                    self.collection_view_items.append({"type": "card", "path": card})
            return

        if self.collection_root_dir and self.current_collection_dir:
            current = os.path.abspath(self.current_collection_dir)
            root = os.path.abspath(self.collection_root_dir)
            if current != root:
                parent = os.path.dirname(current)
                self.collection_listbox.insert(tk.END, "[..]")
                self.collection_view_items.append({"type": "folder", "path": parent})

            for folder_path in self.get_current_child_folders():
                self.collection_listbox.insert(tk.END, f"[Folder] {os.path.basename(folder_path)}")
                self.collection_view_items.append({"type": "folder", "path": folder_path})

            for card in self.get_current_folder_cards():
                self.collection_listbox.insert(tk.END, os.path.basename(card))
                self.collection_view_items.append({"type": "card", "path": card})
            return

        for card in self.collection:
            self.collection_listbox.insert(tk.END, os.path.basename(card))
            self.collection_view_items.append({"type": "card", "path": card})

    def display_card(self, event):
        selected = self.collection_listbox.curselection()
        if selected:
            if selected[0] >= len(self.collection_view_items):
                return
            item = self.collection_view_items[selected[0]]
            if item["type"] != "card":
                return
            self.current_card = item["path"]
            self.preview_current_card()
            self.textbox.delete("1.0", tk.END)
            self.textbox.insert("1.0", self.card_text.get(self.current_card, ""))

    def open_collection_item(self, event):
        index = self.collection_listbox.nearest(event.y)
        if index < 0 or index >= len(self.collection_view_items):
            return
        item = self.collection_view_items[index]
        if item["type"] == "folder":
            self.current_collection_dir = os.path.abspath(item["path"])
            self.update_collection_view()
            self.status_var.set(
                f"Folder: {os.path.relpath(self.current_collection_dir, self.collection_root_dir)}"
            )

    def display_card_from_deck(self, event):
        selected_tab_id = self.tab_control.select()
        if not selected_tab_id:
            return
        subdeck_name = self.tab_control.tab(selected_tab_id, "text")
        deck_listbox = self.subdecks[subdeck_name]["listbox"]
        selected = deck_listbox.curselection()
        if selected:
            display_text = deck_listbox.get(selected[0])
            card_name, _ = display_text.rsplit(" (", 1)
            self.current_card = self.subdecks[subdeck_name]["cards"].get(
                card_name, {}
            ).get("path")
            self.preview_current_card()
            self.textbox.delete("1.0", tk.END)
            self.textbox.insert("1.0", self.card_text.get(self.current_card, ""))

    def get_thumbnail(self, path, rotation, max_size=(600, 600)):
        signature = get_file_signature(path)
        key = (path, rotation, max_size, json.dumps(signature, sort_keys=True))
        cached = self.thumbnail_cache.get(key)
        if cached:
            return cached

        image = Image.open(path)
        if rotation:
            image = image.rotate(-rotation, expand=True)
        image.thumbnail(max_size)
        image_tk = ImageTk.PhotoImage(image)
        self.thumbnail_cache[key] = image_tk
        if len(self.thumbnail_cache) > 200:
            for old_key in list(self.thumbnail_cache.keys())[:50]:
                del self.thumbnail_cache[old_key]
        return image_tk

    def preview_current_card(self):
        if self.current_card and os.path.exists(self.current_card):
            rotation = self.card_rotation.get(self.current_card, 0)
            try:
                image_tk = self.get_thumbnail(self.current_card, rotation)
                self.card_preview.config(image=image_tk, text="")
                self.card_preview.image = image_tk
            except Exception as exc:
                self.card_preview.config(image="", text=f"Preview failed: {exc}")

    def add_card_to_deck(self):
        if not self.current_card:
            return
        selected_tab_id = self.tab_control.select()
        subdeck_name = self.tab_control.tab(selected_tab_id, "text")
        card_name = os.path.basename(self.current_card)
        cards = self.subdecks[subdeck_name]["cards"]
        if card_name in cards:
            cards[card_name]["count"] += 1
        else:
            cards[card_name] = {"path": self.current_card, "count": 1}
        self.update_deck_view(subdeck_name)

    def remove_card_from_deck(self, subdeck_name):
        deck_listbox = self.subdecks[subdeck_name]["listbox"]
        selected = deck_listbox.curselection()
        if not selected:
            return
        display_text = deck_listbox.get(selected[0])
        card_name = display_text.split(" (")[0]
        cards = self.subdecks[subdeck_name]["cards"]
        if card_name in cards:
            if cards[card_name]["count"] > 1:
                cards[card_name]["count"] -= 1
            else:
                del cards[card_name]
        self.update_deck_view(subdeck_name)
        if cards:
            new_selection_index = min(selected[0], deck_listbox.size() - 1)
            deck_listbox.selection_set(new_selection_index)

    def update_deck_view(self, subdeck_name):
        if subdeck_name not in self.subdecks or "listbox" not in self.subdecks[subdeck_name]:
            return
        deck_listbox = self.subdecks[subdeck_name]["listbox"]
        deck_listbox.delete(0, tk.END)
        total_count = 0
        for card_name, info in self.subdecks[subdeck_name]["cards"].items():
            deck_listbox.insert(tk.END, f"{card_name} (x{info['count']})")
            total_count += info["count"]
        self.subdecks[subdeck_name]["count_label"].config(
            text=f"Total cards: {total_count}"
        )

    def new_deck(self):
        for tab_id in list(self.tab_control.tabs()):
            self.tab_control.forget(tab_id)
        self.subdecks = {"Subdeck 1": {"cards": {}}}
        self.add_subdeck_tab("Subdeck 1")

    def save_deck(self):
        deck_title = filedialog.asksaveasfilename(
            title="Save Deck",
            defaultextension=".gdbdeck",
            filetypes=[
                ("GDB deck files", "*.gdbdeck"),
                ("JSON files", "*.json"),
                ("Text files", "*.txt"),
            ],
        )
        if deck_title:
            FileManager.save_deck(deck_title, self.subdecks, self.card_rotation)
            messagebox.showinfo("Success", "Deck saved successfully.")

    def open_deck(self):
        deck_title = filedialog.askopenfilename(
            title="Open Deck",
            filetypes=[("Deck files", "*.gdbdeck *.json *.txt"), ("All files", "*.*")],
        )
        if deck_title:
            loaded_subdecks, loaded_rotations = FileManager.load_deck(deck_title)
            if not loaded_subdecks:
                loaded_subdecks = {"Subdeck 1": {"cards": {}}}
            self.card_rotation.update(loaded_rotations)
            for tab_id in list(self.tab_control.tabs()):
                self.tab_control.forget(tab_id)
            self.subdecks = loaded_subdecks
            for subdeck_name in list(self.subdecks.keys()):
                self.add_subdeck_tab(subdeck_name)

    def create_pdf(self):
        options = {
            "margin": 18,
            "h_spacing": 0,
            "v_spacing": 18,
            "crop": 20,
            "extend_v": 20,
            "extend_h": 0,
            "color": False,
            "image_width_inch": 2.5,
            "image_height_inch": 3.55,
        }
        new_window = tk.Toplevel(self.root)
        new_window.title("Create PDF")

        settings_frame = tk.Frame(new_window)
        settings_frame.pack(fill=tk.BOTH, expand=True)
        opt_entries = {}
        for field, value in options.items():
            row = tk.Frame(settings_frame)
            row.pack(side=tk.TOP, fill=tk.X)
            tk.Label(
                row, width=20, text=field.replace("_", " ").capitalize() + ":", anchor=tk.W
            ).pack(side=tk.LEFT)
            ent = tk.Entry(row)
            ent.pack(side=tk.RIGHT, expand=tk.YES, fill=tk.X)
            ent.insert(0, value)
            opt_entries[field] = ent

        orientation_var = tk.StringVar(value="landscape")
        tk.Radiobutton(
            new_window, text="Portrait", variable=orientation_var, value="portrait"
        ).pack()
        tk.Radiobutton(
            new_window, text="Landscape", variable=orientation_var, value="landscape"
        ).pack()

        def apply_settings():
            try:
                for key in opt_entries:
                    value = opt_entries[key].get()
                    options[key] = float(value) if "." in value else int(value)
                options["orientation"] = orientation_var.get()
                images_paths = [
                    info["path"]
                    for subdeck_info in self.subdecks.values()
                    for info in subdeck_info["cards"].values()
                    for _ in range(info["count"])
                ]
                images = [Image.open(path) for path in images_paths]
                output_file = filedialog.asksaveasfilename(
                    defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")]
                )
                if output_file:
                    PDFManager(images, output_file, options).create_pdf()
                    messagebox.showinfo("Success", "PDF created successfully.")
                new_window.destroy()
            except Exception as exc:
                messagebox.showerror("PDF Error", f"Failed to create PDF: {exc}")

        tk.Button(new_window, text="Apply", command=apply_settings).pack(fill=tk.X)


if __name__ == "__main__":
    root = tk.Tk()
    app = CardApp(root)
    root.mainloop()

import os
import io
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from PIL import Image, ImageTk
from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import pytesseract
import cv2 as opencv
import sys
# from textblob import TextBlob
import numpy as np

def get_resource_path(relative_path):
    base_path = os.path.dirname(sys.executable)
    return os.path.join(base_path, relative_path)

    
# Specify the local Tesseract path
tesseract_path = get_resource_path(os.path.join("Tesseract", "tesseract.exe"))
dll_dir = get_resource_path(".")  # Root directory where DLLs are located
tessdata_dir = get_resource_path(os.path.join("Tesseract", "tessdata"))

# Tell pytesseract where Tesseract and its data are located
pytesseract.pytesseract.tesseract_cmd = tesseract_path
print("Using Tesseract at:", tesseract_path)
print("Exists:", os.path.exists(tesseract_path))


#Comment this line if creating .exe
#pytesseract.pytesseract.tesseract_cmd = "D:/Tesseract/tesseract.exe"
os.environ["TESSDATA_PREFIX"] = tessdata_dir
os.environ["PATH"] = os.pathsep.join([dll_dir, os.environ.get("PATH", "")])




class ImageText:
    """Handles image preprocessing and text extraction."""

    @staticmethod
    def resize_image(self, img, width, height):
        orig_height, orig_width = img.shape
        aspect_ratio = orig_width / orig_height
        if width / height > aspect_ratio:
            width = int(height * aspect_ratio)
        else:
            height = int(width / aspect_ratio)
        return opencv.resize(img, (width, height), interpolation=opencv.INTER_NEAREST)
    
    @staticmethod
    def preprocess_image(image_path, me, rotation=0):
        """Preprocess the image and return a processed version."""
        image = opencv.imread(image_path, opencv.IMREAD_GRAYSCALE)
        #binary = opencv.adaptiveThreshold(image, 255, opencv.ADAPTIVE_THRESH_GAUSSIAN_C, opencv.THRESH_BINARY, 3, 6)
        if image is None:
            raise ValueError(f"Failed to load image at {image_path}")
        if rotation != 0:
            if rotation == 90:
                image = opencv.rotate(image, opencv.ROTATE_90_CLOCKWISE)
            elif rotation == 180:
                image = opencv.rotate(image, opencv.ROTATE_180)
            elif rotation == 270:
                image = opencv.rotate(image, opencv.ROTATE_90_COUNTERCLOCKWISE)
        #opencv.imshow("Binary",binary)
        width = int(image.shape[1] * me.scale_percent / 100)
        height = int(image.shape[0] * me.scale_percent / 100)
        image = opencv.resize(image, (width, height), interpolation=opencv.INTER_LINEAR)

        
        image = opencv.GaussianBlur(image, (me.blur_size, me.blur_size), 0)
               
        
        image = opencv.adaptiveThreshold(
            image, 255, opencv.ADAPTIVE_THRESH_GAUSSIAN_C, 
            opencv.THRESH_BINARY, me.block_size, me.c_value)
    
        black_pixels_mask = image == 0
        image = np.full_like(image, 255)
        image[black_pixels_mask] = 0
        return image
    
    @staticmethod
    def extract_text(image):
        """Extract text from the given image."""
        raw_text = pytesseract.image_to_string(image).strip()

        # # Create a TextBlob object
        # text_blob = TextBlob(raw_text)

        # # # Correct the text
        # corrected_text = text_blob.correct()
        #print(str(corrected_text))
        return str(raw_text)
        #return raw_text


class FileManager:
    """Handles file operations such as loading and saving collections."""

    @staticmethod
    def load_image_files(directory, include_subfolders):
        """Load image files from the given directory."""
        extensions = ('png', 'jpg', 'jpeg', 'gif')
        files = []

        if include_subfolders:
            for root, _, filenames in os.walk(directory):
                for filename in filenames:
                    if filename.endswith(extensions):
                        files.append(os.path.join(root, filename))
        else:
            files = [
                os.path.join(directory, f) for f in os.listdir(directory)
                if f.endswith(extensions)
            ]

        return files

    @staticmethod
    def save_collection(collection, card_text, filepath, card_rotation):
        """Save the card collection along with text to a file."""
        with open(filepath, 'w') as f:
            for card_path, text in card_text.items():
                rotation = card_rotation.get(card_path, 0)
                f.write(f"{card_path}|{text}|{rotation}\n")

    @staticmethod
    def load_collection(filepath):
        collection = []
        card_text = {}
        card_rotation = {}
    
        with open(filepath, 'r') as f:
            for line in f:
                stripped_line = line.strip()
                parts = stripped_line.split('|')
    
                if len(parts) >= 2:
                    card_path = parts[0]
                    text = parts[1]
                    rotation = int(parts[2]) if len(parts) > 2 else 0
    
                    collection.append(card_path)
                    card_text[card_path] = text.replace('@','\n')
                    card_rotation[card_path] = rotation
    
        return collection, card_text, card_rotation


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
            pagesize=landscape(letter) if self.options['orientation'] == 'landscape' else letter
        )
        self.layout_images_on_pdf(c, processed_images)
        c.save()

    def prepare_images(self):
        processed_images = []
        for img in self.images:
            cropped_img = img.crop(
                (
                    self.options['crop'],
                    self.options['crop'],
                    img.width - self.options['crop'],
                    img.height - self.options['crop']
                )
            )

            extended_img = Image.new('RGB', (cropped_img.width + 2 * self.options['extend_h'], cropped_img.height + 2 * self.options['extend_v']), (0, 0, 0))
            extended_img.paste(cropped_img, (self.options['extend_h'], self.options['extend_v']))
            processed_images.append(extended_img)
        return processed_images

    def layout_images_on_pdf(self, canvas_obj, processed_images):
        image_width = self.options['image_width_inch'] * 72  # Convert inches to points
        image_height = self.options['image_height_inch'] * 72

        num_columns = int((canvas_obj._pagesize[0] - 2 * self.options['margin'] + self.options['h_spacing']) // (image_width + self.options['h_spacing']))
        num_rows = int((canvas_obj._pagesize[1] - 2 * self.options['margin'] + self.options['v_spacing']) // (image_height + self.options['v_spacing']))

        x_start = (canvas_obj._pagesize[0] - (num_columns * image_width + self.options['h_spacing'] * (num_columns - 1))) / 2
        y_start = (canvas_obj._pagesize[1] - 2 * image_height + image_height * num_rows + self.options['v_spacing'] * (num_rows - 1)) / 2

        curr_count = 0
        for img in processed_images:
            if curr_count > 0 and curr_count % (num_columns * num_rows) == 0:
                canvas_obj.showPage()

            col = curr_count % num_columns
            row = curr_count // num_columns % num_rows

            img_stream = io.BytesIO()
            img.convert("RGB").save(img_stream, format='PNG')
            img_stream.seek(0)

            canvas_obj.drawImage(ImageReader(img_stream), x_start + col * (image_width + self.options['h_spacing']), y_start - row * (image_height + self.options['v_spacing']), image_width, image_height)

            curr_count += 1


class CardApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Generic TCG Deckbuilder")
        self.collection = []
        self.subdecks = {"Subdeck 1": {'cards': {}}}
        self.current_card = None
        self.card_text = {}
        self.card_rotation = {}  # NEW: store rotation per image (degrees)
        
        # Initialize image processing parameters with default values
        self.scale_percent = 200
        self.blur_size = 7
        self.block_size = 11
        self.c_value = 7
        
        self.root.geometry("1200x800")
        self.root.resizable(True, True)

        self.setup_ui()
        self.deck_title_var = tk.StringVar()

    def setup_ui(self):
        self.setup_menu()
        self.setup_main_pane()

    def setup_menu(self):
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="New Deck", command=self.new_deck)
        file_menu.add_command(label="Save Deck", command=self.save_deck)
        file_menu.add_command(label="Open Deck", command=self.open_deck)
        file_menu.add_command(label="Create PDF", command=self.create_pdf)
        file_menu.add_command(label="Save Collection File", command=self.save_collection)
        file_menu.add_command(label="Load Collection File", command=self.load_collection_file)
        file_menu.add_command(label="Adjust Processing Parameters", command=self.open_slider_window)  # Add this line
        menu.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menu)
        
    def rotate_current_card(self):
        if not self.current_card:
            return
        
        # Rotate in 90° increments
        current_rotation = self.card_rotation.get(self.current_card, 0)
        new_rotation = (current_rotation + 90) % 360
        self.card_rotation[self.current_card] = new_rotation
    
        self.preview_current_card()  # Refresh preview
        
    def setup_main_pane(self):
        top_frame = tk.Frame(self.root)
        top_frame.pack(pady=5, fill=tk.X)

        tk.Label(top_frame, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.update_collection_view)
        self.search_entry = tk.Entry(top_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.load_button = tk.Button(top_frame, text="Load Card Images", command=self.load_cards)
        self.load_button.pack(side=tk.LEFT)

        self.include_subfolders = tk.BooleanVar()
        self.subfolder_check = tk.Checkbutton(top_frame, text="Include Subfolders", variable=self.include_subfolders)
        self.subfolder_check.pack(side=tk.LEFT)

        load_card_text_button = tk.Button(top_frame, text="Make Image Text Searchable", command=self.load_card_text)
        load_card_text_button.pack(side=tk.LEFT)

        main_paned_window = tk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned_window.pack(fill=tk.BOTH, expand=True)

        collection_frame, deck_frame, self.card_preview = self.setup_collection_and_deck_widgets(main_paned_window)
        main_paned_window.add(collection_frame)
        main_paned_window.add(self.card_preview)
        main_paned_window.add(deck_frame)
        
        
        # Assuming the card preview frame is set to a Label
        # Create a frame to hold both the image preview and text box
        preview_container = tk.Frame(self.card_preview)
        preview_container.pack(fill=tk.BOTH, expand=True)
        # Add rotate button under preview
        rotate_button = tk.Button(preview_container, text="Rotate 90°", command=self.rotate_current_card)
        rotate_button.pack(side=tk.BOTTOM, fill=tk.X)
    
        # Card Preview
        self.card_preview = tk.Label(preview_container, text="Select a card to preview", bg="gray", width=80, height=20)
        self.card_preview.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    
        # Create the Text modification box below the preview
        self.textbox = tk.Text(preview_container, wrap=tk.WORD, height=8)
        self.textbox.pack(side=tk.BOTTOM, fill=tk.BOTH, padx=5, pady=5)
        self.textbox.bind("<KeyRelease>", self.update_card_text)

    def open_slider_window(self):
        slider_window = tk.Toplevel(self.root)
        slider_window.title("Adjust Image Processing Parameters")
        
        tk.Label(slider_window, text="Scale Percent:").pack()
        scale_percent_slider = tk.Scale(slider_window, from_=100, to_=300, orient='horizontal')
        scale_percent_slider.set(self.scale_percent)
        scale_percent_slider.pack()
        
        tk.Label(slider_window, text="Blur Size:").pack()
        blur_size_slider = tk.Scale(slider_window, from_=1, to_=21, orient='horizontal', resolution=2)
        blur_size_slider.set(self.blur_size)
        blur_size_slider.pack()
        
        tk.Label(slider_window, text="Block Size:").pack()
        block_size_slider = tk.Scale(slider_window, from_=3, to_=21, orient='horizontal', resolution=2)
        block_size_slider.set(self.block_size)
        block_size_slider.pack()
        
        tk.Label(slider_window, text="C Value:").pack()
        c_value_slider = tk.Scale(slider_window, from_=1, to_=10, orient='horizontal')
        c_value_slider.set(self.c_value)
        c_value_slider.pack()
        
        def apply_changes():
            self.scale_percent = scale_percent_slider.get()
            self.blur_size = blur_size_slider.get()
            self.block_size = block_size_slider.get()
            self.c_value = c_value_slider.get()
            messagebox.showinfo("Parameters Applied", "Image processing parameters updated successfully.")
        
        def update_ocr_text():
            if self.current_card:
                try:
                    scale_percent_original = self.scale_percent
                    self.scale_percent = scale_percent_slider.get()
                    blur_size_original = self.blur_size
                    self.blur_size = blur_size_slider.get()
                    block_size_original = self.block_size
                    self.block_size = block_size_slider.get()
                    c_value_original = self.c_value
                    self.c_value = c_value_slider.get()
                    
                    rotation = self.card_rotation.get(self.current_card, 0)
                    processed_image = ImageText.preprocess_image(self.current_card, self, rotation)
                    text = ImageText.extract_text(processed_image)
                    if text:
                        self.textbox.delete("1.0", tk.END)
                        self.textbox.insert("1.0", text.strip())
                        self.card_text[self.current_card] = text.strip()
                        #print("new text:\n"+text)
                    self.scale_percent = scale_percent_original 
                    self.blur_size = blur_size_original
                    self.block_size = block_size_original
                    self.c_value = c_value_original
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to process and extract text: {str(e)}")
    
        apply_button = tk.Button(slider_window, text="Apply", command=apply_changes)
        apply_button.pack()
    
        update_button = tk.Button(slider_window, text="Test Paramters", command=update_ocr_text)
        update_button.pack()
    
        # Set initial values
        self.scale_percent = scale_percent_slider.get()
        self.blur_size = blur_size_slider.get()
        self.block_size = block_size_slider.get()
        self.c_value = c_value_slider.get()
        
    def update_card_text(self, event=None):
        if self.current_card:
            updated_text = self.textbox.get("1.0", tk.END).strip()
            self.card_text[self.current_card] = updated_text

    def setup_collection_and_deck_widgets(self, paned_window):
        collection_frame = tk.Frame(paned_window)
        tk.Label(collection_frame, text="Collection:", width = 40).pack(anchor=tk.W)
        self.collection_listbox = tk.Listbox(collection_frame)
        self.collection_listbox.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.collection_listbox.bind("<<ListboxSelect>>", self.display_card)
        collection_scrollbar = tk.Scrollbar(collection_frame)
        collection_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.collection_listbox.config(yscrollcommand=collection_scrollbar.set)
        collection_scrollbar.config(command=self.collection_listbox.yview)

        self.add_to_deck_button = tk.Button(collection_frame, text="Add to Deck", command=self.add_card_to_deck)
        self.add_to_deck_button.pack(fill=tk.X)

        card_preview = tk.Label(paned_window, text="Select a card to preview", bg="gray", width=80, height=20)
        deck_frame = self.setup_deck_section(paned_window)

        return collection_frame, deck_frame, card_preview

    def setup_deck_section(self, paned_window):
        deck_frame = tk.PanedWindow(paned_window, orient=tk.VERTICAL)

        self.tab_control = ttk.Notebook(deck_frame)
        self.add_subdeck_tab("Subdeck 1")
        self.tab_control.pack(fill=tk.BOTH, expand=True)

        add_subdeck_button = tk.Button(deck_frame, text="Add Subdeck", command=self.add_new_subdeck)
        add_subdeck_button.pack(side=tk.BOTTOM, fill=tk.X)

        return deck_frame

    def load_card_text(self):
        if not messagebox.askokcancel("Load Card Text", "Loading card text can take a while. Do you want to proceed?"):
            return
        for card_path in self.collection:
            try:
                rotation = self.card_rotation.get(card_path, 0)
                processed_image = ImageText.preprocess_image(card_path, self, rotation)
                text = ImageText.extract_text(processed_image)

                if text:
                    cleaned_text = cleaned_text = text.replace('|', '').replace('@','')
                    self.card_text[card_path] = cleaned_text

            except Exception as e:
                print(f"Error processing {card_path}: {e}")

        self.update_collection_view()
        messagebox.showinfo("Success", "Card Text Loaded")

    def save_collection(self):
    # Preprocess the card_text to replace new lines with slashes
        collection_file = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if collection_file:
            processed_card_text = {key: text.replace('\n', '@') for key, text in self.card_text.items()}
            FileManager.save_collection(self.collection, processed_card_text, collection_file, self.card_rotation)
            messagebox.showinfo("Success", "Collection saved successfully")


    def load_collection_file(self):
        collection_file = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if collection_file:
            self.collection, self.card_text, self.card_rotation = FileManager.load_collection(collection_file)
            self.update_collection_view()
    def view_readme(self):
        pass
    
    def add_subdeck_tab(self, subdeck_name):
        tab_frame = tk.Frame(self.tab_control)
        self.tab_control.add(tab_frame, text=subdeck_name)

        deck_listbox = tk.Listbox(tab_frame)
        deck_listbox.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        deck_listbox.bind("<<ListboxSelect>>", self.display_card_from_deck)
        self.subdecks[subdeck_name] = {'frame': tab_frame, 'listbox': deck_listbox, 'cards': {}}

        button_frame = tk.Frame(tab_frame)
        button_frame.pack(fill=tk.X)

        remove_from_deck_button = tk.Button(
            button_frame, text="Remove Card from Deck",
            command=lambda: self.remove_card_from_deck(subdeck_name)
        )
        remove_from_deck_button.pack(side=tk.LEFT, fill=tk.X)

        rename_button = tk.Button(
            button_frame, text="Rename Subdeck",
            command=lambda: self.rename_subdeck(subdeck_name)
        )
        rename_button.pack(side=tk.LEFT, fill=tk.X)

        delete_button = tk.Button(
            button_frame, text="Delete Subdeck",
            command=lambda: self.delete_subdeck(subdeck_name)
        )
        delete_button.pack(side=tk.LEFT, fill=tk.X)
        
        # Add a label to display the total count of cards
        self.subdecks[subdeck_name]['count_label'] = tk.Label(tab_frame, text="Total cards: 0")
        self.subdecks[subdeck_name]['count_label'].pack(side=tk.BOTTOM, fill=tk.X)

    def add_new_subdeck(self):
        new_subdeck_name = f"Subdeck {len(self.subdecks) + 1}"
        self.subdecks[new_subdeck_name] = {'cards': {}}
        self.add_subdeck_tab(new_subdeck_name)

            
    def rename_subdeck(self, subdeck_name):
        new_name = simpledialog.askstring("Rename Subdeck", "Enter new name:")
        if new_name and new_name not in self.subdecks:
            # Change the internal dictionary key
            self.subdecks[new_name] = self.subdecks.pop(subdeck_name)
            # Update the tab title
            tab_frame = self.subdecks[new_name]['listbox'].master
            tab_index = self.tab_control.index(tab_frame)
            self.tab_control.tab(tab_index, text=new_name)
            # Update the buttons in the tab to reference the new subdeck name
            button_frame = tab_frame.winfo_children()[-2]  # Assuming the last child is the button frame
            delete_button = button_frame.winfo_children()[-1]  # Assuming the last button is the delete button
            delete_button.config(command=lambda: self.delete_subdeck(new_name))
            remove_button = button_frame.winfo_children()[0]  # Assuming the first button is the remove button
            remove_button.config(command=lambda: self.remove_card_from_deck(new_name))

    def delete_subdeck(self, subdeck_name):
        if len(self.subdecks) > 1:
            tab_frame = self.subdecks[subdeck_name]['listbox'].master
            tab_index = self.tab_control.index(tab_frame)
            self.tab_control.forget(tab_index)
            del self.subdecks[subdeck_name]

    def load_cards(self):
        self.card_text.clear()
        directory = filedialog.askdirectory(title="Select Card Folder")
        if directory:
            self.collection = FileManager.load_image_files(directory, self.include_subfolders.get())
            self.update_collection_view()

    def update_collection_view(self, *_):
        search_query = self.search_var.get().lower()
        self.collection_listbox.delete(0, tk.END)
        for card in self.collection:
            card_name = os.path.basename(card)
            extracted_text = self.card_text.get(card, "")
            if search_query in card_name.lower() or search_query in extracted_text.lower():
                self.collection_listbox.insert(tk.END, card_name)


    def display_card(self, event):
        selected = self.collection_listbox.curselection()
        if selected:
            card_name = self.collection_listbox.get(selected[0])
            self.current_card = next((card_path for card_path in self.collection if os.path.basename(card_path) == card_name), None)
            self.preview_current_card()
            # Load existing text into the textbox
            card_text = self.card_text.get(self.current_card, "")
            self.textbox.delete("1.0", tk.END)
            self.textbox.insert("1.0", card_text)

    def display_card_from_deck(self, event):
        selected_tab_id = self.tab_control.select()
        subdeck_name = self.tab_control.tab(selected_tab_id, "text")
        deck_listbox = self.subdecks[subdeck_name]['listbox']
        selected = deck_listbox.curselection()
        if selected:
            display_text = deck_listbox.get(selected[0])
            card_name, _ = display_text.rsplit(' (', 1)
            self.current_card = self.subdecks[subdeck_name]['cards'].get(card_name, {}).get('path')
            self.preview_current_card()
    

    def preview_current_card(self):
        if self.current_card and os.path.exists(self.current_card):
            image = Image.open(self.current_card)
            # NEW: apply rotation
            rotation = self.card_rotation.get(self.current_card, 0)
            if rotation != 0:
                image = image.rotate(-rotation, expand=True)
            image.thumbnail((600, 600))
            image_tk = ImageTk.PhotoImage(image)
            self.card_preview.config(image=image_tk, text = "")
            self.card_preview.image = image_tk

    def add_card_to_deck(self):
        if not self.current_card:
            return

        selected_tab_id = self.tab_control.select()
        subdeck_name = self.tab_control.tab(selected_tab_id, "text")
        card_name = os.path.basename(self.current_card)

        if card_name in self.subdecks[subdeck_name]['cards']:
            self.subdecks[subdeck_name]['cards'][card_name]['count'] += 1
        else:
            self.subdecks[subdeck_name]['cards'][card_name] = {'path': self.current_card, 'count': 1}

        self.update_deck_view(subdeck_name)

    def remove_card_from_deck(self, subdeck_name):
        deck_listbox = self.subdecks[subdeck_name]['listbox']
        selected = deck_listbox.curselection()
        if selected:
            display_text = deck_listbox.get(selected[0])
            card_name = display_text.split(' (')[0]
            if card_name in self.subdecks[subdeck_name]['cards']:
                if self.subdecks[subdeck_name]['cards'][card_name]['count'] > 1:
                    self.subdecks[subdeck_name]['cards'][card_name]['count'] -= 1
                else:
                    del self.subdecks[subdeck_name]['cards'][card_name]
            self.update_deck_view(subdeck_name)
        # Re-select the item at the same index
        # Check if the listbox is still populated and has an entry at the deleted item's index
        if self.subdecks[subdeck_name]['cards']:  # Ensure there are still cards left in the subdeck
            new_selection_index = min(selected[0], deck_listbox.size() - 1)
            deck_listbox.selection_set(new_selection_index)

    def update_deck_view(self, subdeck_name):
        deck_listbox = self.subdecks[subdeck_name]['listbox']
        deck_listbox.delete(0, tk.END)
        total_count = 0
        for card_name, info in self.subdecks[subdeck_name]['cards'].items():
            display_text = f"{card_name} (x{info['count']})"
            deck_listbox.insert(tk.END, display_text)
            total_count += info['count']
        # Update the total count label
        self.subdecks[subdeck_name]['count_label'].config(text=f"Total cards: {total_count}")

    def new_deck(self):
        for tab_id in list(self.tab_control.tabs()):
            self.tab_control.forget(tab_id)
        self.subdecks = {"Subdeck 1": {'cards': {}}}
        self.add_subdeck_tab("Subdeck 1")

    def save_deck(self):
        deck_title = filedialog.asksaveasfilename(title="Save Deck", defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if deck_title:
            with open(deck_title, 'w') as f:
                for subdeck_name, subdeck_info in self.subdecks.items():
                    f.write(f"Subdeck: {subdeck_name}\n")
                    for card_name, info in subdeck_info['cards'].items():
                        f.write(f"{info['path']} x{info['count']} r{self.card_rotation.get(info['path'], 0)}\n")
            messagebox.showinfo("Success", "Deck saved successfully")

    def open_deck(self):
        deck_title = filedialog.askopenfilename(title="Open Deck", filetypes=[("Text files", "*.txt")])
        if deck_title:
            with open(deck_title, 'r') as f:
                lines = f.readlines()
                self.new_deck()
                Subdeck1Used = False
                current_subdeck = None
                for line in lines:
                    if line.startswith("Subdeck:"):
                        subdeck_name = line.split(":", 1)[-1].strip()
                        if subdeck_name not in self.subdecks:
                            self.subdecks[subdeck_name] = {'cards': {}}
                            self.add_subdeck_tab(subdeck_name)
                        else:
                            Subdeck1Used = True
                        current_subdeck = subdeck_name
                    else:
                        if current_subdeck:
                            path_count = line.rsplit(' x', 1)
                            path = path_count[0]
                            
                            count_rot = path_count[1].strip().split(' r')
                            count = int(count_rot[0])
                            rotation = int(count_rot[1]) if len(count_rot) > 1 else 0
                            
                            card_name = os.path.basename(path)
                            self.subdecks[current_subdeck]['cards'][card_name] = {'path': path, 'count': count}
                            self.card_rotation[path] = rotation
        if not Subdeck1Used:
            self.delete_subdeck("Subdeck 1")
    def create_pdf(self):
        # def prompt_pdf_settings():
        options = {'margin': 18, 'h_spacing': 0, 'v_spacing': 18, 'crop': 20, 'extend_v': 20, 'extend_h': 0, 'color': False, 'image_width_inch': 2.5, 'image_height_inch': 3.55}
        new_window = tk.Toplevel(self.root)
        new_window.title("Create PDF")

        settings_frame = tk.Frame(new_window)
        settings_frame.pack(fill=tk.BOTH, expand=True)

        opt_entries = {}
        for field, value in options.items():
            row = tk.Frame(settings_frame)
            row.pack(side=tk.TOP, fill=tk.X)
            lbl = tk.Label(row, width=20, text=field.replace('_', ' ').capitalize() + ':', anchor=tk.W)
            lbl.pack(side=tk.LEFT)
            ent = tk.Entry(row)
            ent.pack(side=tk.RIGHT, expand=tk.YES, fill=tk.X)
            ent.insert(0, value)
            opt_entries[field] = ent

        orientation_var = tk.StringVar(value='landscape')
        tk.Radiobutton(new_window, text="Portrait", variable=orientation_var, value="portrait").pack()
        tk.Radiobutton(new_window, text="Landscape", variable=orientation_var, value="landscape").pack()

        def apply_settings():
            for key in opt_entries:
                options[key] = float(opt_entries[key].get()) if '.' in opt_entries[key].get() else int(opt_entries[key].get())
            options['orientation'] = orientation_var.get()
            images_paths = [info['path'] for subdeck_info in self.subdecks.values() for card_name, info in subdeck_info['cards'].items() for _ in range(info['count'])]
            images = [Image.open(path) for path in images_paths]

            output_file = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")])
            if output_file:
                pdf_manager = PDFManager(images, output_file, options)
                pdf_manager.create_pdf()
                messagebox.showinfo("Success", "PDF created successfully!")
            new_window.destroy()
            #return options

        button_frame = tk.Frame(new_window)
        button_frame.pack()

        tk.Button(button_frame, text="Apply", command=apply_settings).pack(fill=tk.X)

        new_window.mainloop()



if __name__ == "__main__":
    root = tk.Tk()
    app = CardApp(root)
    root.mainloop()

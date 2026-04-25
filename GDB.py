#pyinstaller -F GDB.py

import os
import io
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from PIL import Image, ImageTk
from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

class CardApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Generic TCG Deckbuilder")
        self.collection = []
        self.subdecks = {"Subdeck 1": {}}
        self.current_card = None

        # Fix static window size to prevent resizing
        self.root.geometry("1200x800")
        self.root.resizable(True, True)

        # Setup main menu
        self.menu = tk.Menu(root)
        self.filemenu = tk.Menu(self.menu, tearoff=0)
        self.filemenu.add_command(label="New Deck", command=self.new_deck)
        self.filemenu.add_command(label="Save Deck", command=self.save_deck)
        self.filemenu.add_command(label="Open Deck", command=self.open_deck)
        self.filemenu.add_command(label="Create PDF", command=self.create_pdf)
        self.menu.add_cascade(label="File", menu=self.filemenu)
        root.config(menu=self.menu)
        
        # Setup deck and collection widgets
        
        # Top frame setup
        top_frame = tk.Frame(root)
        top_frame.pack(pady=5, fill=tk.X)


        tk.Label(top_frame, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.update_collection_view)
        self.search_entry = tk.Entry(top_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.include_subfolders = tk.BooleanVar()
        self.subfolder_check = tk.Checkbutton(top_frame, text="Include Subfolders", variable=self.include_subfolders)
        self.subfolder_check.pack(side=tk.LEFT)

        self.load_button = tk.Button(top_frame, text="Load Collection", command=self.load_cards)
        self.load_button.pack(side=tk.RIGHT)

      # Replace main_frame with a PanedWindow for resizable panes
        main_paned_window = tk.PanedWindow(root, orient=tk.HORIZONTAL)
        main_paned_window.pack(fill=tk.BOTH, expand=True)
        
        # Collection section
        collection_frame = tk.Frame(main_paned_window)
        
        tk.Label(collection_frame, text="Collection:").pack(anchor=tk.W)
        self.collection_listbox = tk.Listbox(collection_frame)
        self.collection_listbox.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.collection_listbox.bind("<<ListboxSelect>>", self.display_card)
        
        self.collection_scrollbar = tk.Scrollbar(collection_frame)
        self.collection_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.collection_listbox.config(yscrollcommand=self.collection_scrollbar.set)
        self.collection_scrollbar.config(command=self.collection_listbox.yview)
        
        self.add_to_deck_button = tk.Button(collection_frame, text="Add to Deck", command=self.add_card_to_deck)
        self.add_to_deck_button.pack(fill=tk.X)
        main_paned_window.paneconfigure(collection_frame, width=250)
        main_paned_window.add(collection_frame)  # Add Collection pane
        
        # Card preview section
        self.card_preview = tk.Label(main_paned_window, text="Select a card to preview", bg="gray", width=80, height=20)
        
        main_paned_window.add(self.card_preview)  # Add Card Preview pane
        
        # Deck section with nested PanedWindow for Deck and Subdecks
        deck_paned_window = tk.PanedWindow(main_paned_window, orient=tk.VERTICAL)
        
        # Subdecks tab control
        self.tab_control = ttk.Notebook(deck_paned_window, height = 700)
        self.add_subdeck_tab("Subdeck 1")
        self.tab_control.pack(fill=tk.BOTH, expand=True)
        deck_paned_window.add(self.tab_control)
        
        # Button to add new subdeck
        self.add_subdeck_button = tk.Button(deck_paned_window, text="Add Subdeck", command=self.add_new_subdeck)
        self.add_subdeck_button.pack(side=tk.BOTTOM, fill=tk.X)
        deck_paned_window.add(self.add_subdeck_button)
        main_paned_window.paneconfigure(deck_paned_window, width=250)
        main_paned_window.add(deck_paned_window)  # Add Deck pane

        
    def add_subdeck_tab(self, subdeck_name):
        tab_frame = tk.Frame(self.tab_control)
        self.tab_control.add(tab_frame, text=subdeck_name)

        deck_listbox = tk.Listbox(tab_frame)
        deck_listbox.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        deck_listbox.bind("<<ListboxSelect>>", self.display_card_from_deck)
        self.subdecks[subdeck_name] = {'frame': tab_frame, 'listbox': deck_listbox, 'cards': {}}

        button_frame = tk.Frame(tab_frame)
        button_frame.pack(fill=tk.X)

        remove_from_deck_button = tk.Button(button_frame, text="Remove Card from Deck", command=lambda: self.remove_card_from_deck(subdeck_name))
        remove_from_deck_button.pack(side=tk.LEFT, fill=tk.X)

        rename_button = tk.Button(button_frame, text="Rename Subdeck", command=lambda: self.rename_subdeck(subdeck_name))
        rename_button.pack(side=tk.LEFT, fill=tk.X)

        delete_button = tk.Button(button_frame, text="Delete Subdeck", command=lambda: self.delete_subdeck(subdeck_name))
        delete_button.pack(side=tk.LEFT, fill=tk.X)

    def add_new_subdeck(self):
        new_subdeck_name = f"Subdeck {len(self.subdecks) + 1}"
        self.add_subdeck_tab(new_subdeck_name)

    def rename_subdeck(self, subdeck_name):
        new_name = simpledialog.askstring("Rename Subdeck", "Enter new name:")
        if new_name and new_name not in self.subdecks:
            # Change the internal dictionary key
            self.subdecks[new_name] = self.subdecks.pop(subdeck_name)
            # Update the tab title
            tab_frame = self.subdecks[new_name]['frame']
            tab_index = self.tab_control.index(tab_frame)
            self.tab_control.tab(tab_index, text=new_name)
            # Update the buttons in the tab to reference the new subdeck name
            button_frame = tab_frame.winfo_children()[-1]  # Assuming the last child is the button frame
            delete_button = button_frame.winfo_children()[-1]  # Assuming the last button is the delete button
            delete_button.config(command=lambda: self.delete_subdeck(new_name))
            remove_button = button_frame.winfo_children()[0]  # Assuming the first button is the remove button
            remove_button.config(command=lambda: self.remove_card_from_deck(new_name))


    def delete_subdeck(self, subdeck_name):
        if len(self.subdecks) > 1:
            # Remove from the notebook and dictionary
            tab_frame = self.subdecks[subdeck_name]['frame']
            tab_index = self.tab_control.index(tab_frame)
            self.tab_control.forget(tab_index)
            del self.subdecks[subdeck_name]

    def load_cards(self):
        folder = filedialog.askdirectory(title="Select Card Folder")
        if folder:
            self.collection.clear()
            if self.include_subfolders.get():
                for root, _, files in os.walk(folder):
                    for file in files:
                        if file.endswith(('png', 'jpg', 'jpeg', 'gif')):
                            self.collection.append(os.path.join(root, file))
            else:
                self.collection = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(('png', 'jpg', 'jpeg', 'gif'))]

            self.update_collection_view()

    def update_collection_view(self, *_):
        search_query = self.search_var.get().lower()
        self.collection_listbox.delete(0, tk.END)
        for card in self.collection:
            card_name = os.path.basename(card)
            if search_query in card_name.lower():
                self.collection_listbox.insert(tk.END, card_name)

    def display_card(self, event):
        selected = self.collection_listbox.curselection()
        if selected:
            card_name = self.collection_listbox.get(selected[0])
            for card_path in self.collection:
                if os.path.basename(card_path) == card_name:
                    self.current_card = card_path
                    break
            self.preview_current_card()

    def display_card_from_deck(self, event):
        selected_tab_id = self.tab_control.select()
        subdeck_name = self.tab_control.tab(selected_tab_id, "text")
        deck_listbox = self.subdecks[subdeck_name]['listbox']
        selected = deck_listbox.curselection()
        if selected:
            display_text = deck_listbox.get(selected[0])
            card_name, qty_info = display_text.rsplit(' (', 1)
            self.current_card = self.subdecks[subdeck_name]['cards'].get(card_name, {}).get('path')
            self.preview_current_card()

    def preview_current_card(self):
        if self.current_card and os.path.exists(self.current_card):
            image = Image.open(self.current_card)
            image.thumbnail((600, 600))
            image_tk = ImageTk.PhotoImage(image)
            self.card_preview.config(image=image_tk, text="")
            self.card_preview.image = image_tk

    def add_card_to_deck(self):
        if self.current_card:
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

    def update_deck_view(self, subdeck_name):
        deck_listbox = self.subdecks[subdeck_name]['listbox']
        deck_listbox.delete(0, tk.END)
        for card_name, info in self.subdecks[subdeck_name]['cards'].items():
            display_text = f"{card_name} (x{info['count']})"
            deck_listbox.insert(tk.END, display_text)

    def new_deck(self):
        for tab_id in list(self.tab_control.tabs()):
            self.tab_control.forget(tab_id)
        self.subdecks = {"Subdeck 1": {}}
        self.add_subdeck_tab("Subdeck 1")

    def save_deck(self):
        deck_title = filedialog.asksaveasfilename(title="Save Deck", defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if deck_title:
            with open(deck_title, 'w') as f:
                f.write(f"Deck Title: {self.deck_title_var.get()}\n")
                for subdeck_name, subdeck_info in self.subdecks.items():
                    f.write(f"Subdeck: {subdeck_name}\n")
                    for card_name, info in subdeck_info['cards'].items():
                        f.write(f"{info['path']} x{info['count']}\n")
            messagebox.showinfo("Success", "Deck saved successfully")

    def open_deck(self):
        deck_title = filedialog.askopenfilename(title="Open Deck", filetypes=[("Text files", "*.txt")])
        if deck_title:
            with open(deck_title, 'r') as f:
                lines = f.readlines()
                self.new_deck()  # Reset and clear
                current_subdeck = None
                for line in lines:
                    if line.startswith("Deck Title:"):
                        self.deck_title_var.set(line.split(":", 1)[-1].strip())
                    elif line.startswith("Subdeck:"):
                        subdeck_name = line.split(":", 1)[-1].strip()
                        if subdeck_name not in self.subdecks:
                            self.add_subdeck_tab(subdeck_name)
                        current_subdeck = subdeck_name
                    else:
                        if current_subdeck:
                            path_count = line.rsplit(' x', 1)
                            path = path_count[0]
                            count = int(path_count[1].strip())
                            card_name = os.path.basename(path)
                            self.subdecks[current_subdeck]['cards'][card_name] = {'path': path, 'count': count}
                            self.update_deck_view(current_subdeck)

    def create_pdf(self):
        new_window = tk.Toplevel(self.root)
        new_window.title("Create PDF")
        
        # Define variables and their default values
        margin_var = tk.IntVar(value=18)
        h_spacing_var = tk.IntVar(value=0)
        v_spacing_var = tk.IntVar(value=18)
        crop_var = tk.IntVar(value=5)
        extend_v_var = tk.IntVar(value=20)
        extend_h_var = tk.IntVar(value=10)
        width_var = tk.DoubleVar(value=2.5)
        height_var = tk.DoubleVar(value=3.55)
        orientation_var = tk.StringVar(value="landscape")

        # PDF creation form setup
        def run_pdf_creation():
            try:
                margin = int(margin_var.get())
                h_spacing = int(h_spacing_var.get())
                v_spacing = int(v_spacing_var.get())
                crop = int(crop_var.get())
                extend_v = int(extend_v_var.get())
                extend_h = int(extend_h_var.get())
                image_width_inch = float(width_var.get())
                image_height_inch = float(height_var.get())
                orientation = orientation_var.get()
                
                images_paths = []
                for subdeck_info in self.subdecks.values():
                    images_paths.extend(
                        [info['path'] for card_name, info in subdeck_info['cards'].items() for _ in range(info['count'])]
                    )
                
                images = [Image.open(path) for path in images_paths]
                output_file = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")])
                if output_file:
                    self.process_images_and_create_pdf(images, output_file, margin, h_spacing, v_spacing, image_width_inch, image_height_inch, crop, extend_v, extend_h, orientation)
                    messagebox.showinfo("Success", "PDF created successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"An error occurred: {e}")

        tk.Label(new_window, text="Margin:").pack()
        tk.Entry(new_window, textvariable=margin_var).pack()

        tk.Label(new_window, text="Horizontal Spacing:").pack()
        tk.Entry(new_window, textvariable=h_spacing_var).pack()

        tk.Label(new_window, text="Vertical Spacing:").pack()
        tk.Entry(new_window, textvariable=v_spacing_var).pack()

        tk.Label(new_window, text="Image Width (inches):").pack()
        tk.Entry(new_window, textvariable=width_var).pack()

        tk.Label(new_window, text="Image Height (inches):").pack()
        tk.Entry(new_window, textvariable=height_var).pack()

        tk.Label(new_window, text="Crop (px):").pack()
        tk.Entry(new_window, textvariable=crop_var).pack()

        tk.Label(new_window, text="Extend Vertical (px):").pack()
        tk.Entry(new_window, textvariable=extend_v_var).pack()

        tk.Label(new_window, text="Extend Horizontal (px):").pack()
        tk.Entry(new_window, textvariable=extend_h_var).pack()

        tk.Label(new_window, text="Orientation:").pack()
        tk.Radiobutton(new_window, text="Portrait", variable=orientation_var, value="portrait").pack()
        tk.Radiobutton(new_window, text="Landscape", variable=orientation_var, value="landscape").pack()

        tk.Button(new_window, text="Create PDF", command=run_pdf_creation).pack(pady=10)

    def process_images_and_create_pdf(self, images, output_pdf_path, margin, h_spacing, v_spacing, image_width_inch, image_height_inch, crop, extend_v, extend_h, orientation):
        processed_images = []
        for img in images:
            if crop > 0:
                width, height = img.size
                img = img.crop((crop, crop, width - crop, height - crop))

            new_width = img.width + extend_h * 2
            new_height = img.height + extend_v * 2
            size = (new_width, new_height)
            new_img = Image.new('RGB', size, (0, 0, 0))
            new_img.paste(img, (extend_h, extend_v))
            processed_images.append(new_img)

        if orientation == "landscape":
            c = canvas.Canvas(output_pdf_path, pagesize=landscape(letter))
            page_width, page_height = landscape(letter)
        else:
            c = canvas.Canvas(output_pdf_path, pagesize=letter)
            page_width, page_height = letter

        image_width = image_width_inch * 72  # Convert inches to points (72 points per inch)
        image_height = image_height_inch * 72

        num_columns = int((page_width - 2 * margin + h_spacing) // (image_width + h_spacing))
        num_rows = int((page_height - 2 * margin + v_spacing) // (image_height + v_spacing))

        x_start = (page_width - ((num_columns * image_width) + ((num_columns - 1) * h_spacing))) / 2
        y_start = (page_height + image_height * num_rows + v_spacing * (num_rows - 1)) / 2

        count = 0
        for img in processed_images:
            if count > 0 and count % (num_columns * num_rows) == 0:
                c.showPage()

            idx = count % (num_columns * num_rows)
            col = idx % num_columns
            row = idx // num_columns

            img_rgb = img.convert("RGB")
            img_stream = io.BytesIO()
            img_rgb.save(img_stream, format='PNG')
            img_stream.seek(0)

            img_reader = ImageReader(img_stream)

            x = x_start + col * (image_width + h_spacing)
            y = y_start - row * (image_height + v_spacing) - image_height
            c.drawImage(img_reader, x, y, image_width, image_height)

            count += 1

        c.save()

if __name__ == "__main__":
    root = tk.Tk()
    app = CardApp(root)
    root.mainloop()


import csv
import json
import multiprocessing
import queue
import shutil
import sys
import threading

# Block PyQt5 - force PySide2 usage
class BlockPyQt5:
    def find_module(self, fullname, path=None):
        if fullname.startswith('PyQt5'):
            raise ImportError(f"PyQt5 is blocked - using PySide2 instead")

sys.meta_path.insert(0, BlockPyQt5())

import tkinter as tk

from boundingbox import BoundingBox
from cache import cache_init, cache_get_valid, cache_save, cache_get_row
from characteristics import excluded_columns, get_characteristics, get_hash
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from itertools import chain
from pathlib import Path
from PIL import Image as PILImage, ImageTk
from tkinter import *
from tkinter import ttk, filedialog, messagebox, simpledialog

RULES_DIR = Path(__file__).parent / "rules"
RULES_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = Path(__file__).parent / "settings.json"

MAX_WORKERS = max(1, multiprocessing.cpu_count() - 1)

detached_categorized_items = []
rules_category_map = {}
supress_preview = False
last_preview_path = None
_display_to_category_map = {}
_category_to_display_map = {}

def analyze_button_update(*args):
    """Enable/disable Add Rule button based on char_table selection"""
    folder_gallery = folder_gallery_path.get()
    folder_step = folder_step_path.get()
    folder_category = category_var.get()

    if folder_gallery and folder_step and folder_category:
        analyze_button.configure(state="normal")
    else:
        analyze_button.configure(state="disabled")

def analyze_start(mode="analyze", *args):
    """Start analysis in background thread"""
    if hasattr(analyze_start, 'running') and analyze_start.running:
        analyze_stop()
        return  # Already running
    
    analyze_start.running = True
    analyze_start.mode = mode

    analyze_button.configure(state="normal" if mode == "analyze" else "disabled")
    categorize_button.configure(state="normal" if mode == "categorize" else "disabled")
    reclassify_button.configure(state="normal" if mode == "reclassify" else "disabled")
    analyze_button.configure(text="Stop" if mode == "analyze" else "Analyze")
    categorize_button.configure(text="Stop" if mode == "categorize" else "Categorize")
    reclassify_button.configure(text="Stop" if mode == "reclassify" else "Reclassify")
    generate_gallery_button.configure(state="disabled")
    generate_csv_button.configure(state="disabled")
    
    # Create queue for communication between threads
    analyze_start.result_queue = queue.Queue()
    analyze_start.stop_flag = threading.Event()
    
    # Start background thread
    thread = threading.Thread(target=analyze_worker, daemon=True)
    thread.start()

    # Clear existing table entries
    if mode == "analyze":
        char_table_clear()
    
    # Start GUI update loop
    analyze_update()

def analyze_stop():
    """Stop the analysis process"""
    if hasattr(analyze_start, 'stop_flag'):
        analyze_start.stop_flag.set()
        analyze_button.configure(text="Analyze")
        categorize_button.configure(text="Categorize")
        reclassify_button.configure(text="Reclassify")
        progress_label.config(text="Stopped")
        analyze_start.running = False

def analyze_update():
    """Check for results from background thread and update GUI"""
    try:
        # Process at most N messages per tick to avoid long UI stalls
        for _ in range(20):
            msg_type, *args = analyze_start.result_queue.get_nowait()
            
            if msg_type == 'progress':
                current, total, filename = args
                progress_bar['value'] = current
                progress_bar['maximum'] = total
                mode = getattr(analyze_start, 'mode', 'analyze')
                action = "Reclassifying" if mode == "reclassify" else "Categorizing" if mode == "categorize" else "Processing"
                progress_label.config(text=f"{action} {current}/{total}: {filename}")
                color_instances_check.configure(state="disabled")
                hide_categorized_check.configure(state="disabled")
                file_limit_check.configure(state="disabled")
                
            elif msg_type == 'result':
                characteristics = args[0]
                char_table_insert(characteristics)

            elif msg_type == 'reclassify_result':
                item, characteristics = args
                char_table_row_update(item, characteristics)
                summary_stats_update()

            elif msg_type == 'categorize_result':
                item = args[0]
                char_table.delete(item)
                summary_stats_update()
                
            elif msg_type == 'complete':
                success_count, skipped_count, categorized_count = args
                progress_bar['value'] = 0
                progress_label.config(text=f"Done: {success_count} processed, {categorized_count} Categorized, {skipped_count} skipped")
                analyze_button.configure(state="normal", text="Analyze")
                categorize_button.configure(state="normal", text="Categorize")
                analyze_start.running = False
                reclassify_button_update()
                generate_gallery_button.configure(state="normal")
                generate_csv_button.configure(state="normal")

                # Re-enable color checkboxes and apply colors if enabled
                file_limit_check.configure(state="normal")
                hide_categorized_check.configure(state="normal")
                color_instances_check.configure(state="normal")
                if color_instances.get():
                    char_table_row_color()

                rule_populate()
                summary_stats_update()

                return  # Stop checking
                
            elif msg_type == 'error':
                error_msg = args[0]
                messagebox.showerror("Analysis Error", f"Error during analysis: {error_msg}")
                analyze_button.configure(state="normal", text="Analyze")
                categorize_button.configure(state="normal", text="Categorize")
                generate_gallery_button.configure(state="normal")
                generate_csv_button.configure(state="normal")
                reclassify_button_update()
                analyze_start.running = False
                color_instances_check.configure(state="normal")
                hide_categorized_check.configure(state="normal")
                file_limit_check.configure(state="normal")
                return
                
    except queue.Empty:
        pass  # No messages available
    
    # Schedule next check
    root.after(25, analyze_update)

def analyze_worker():
    """Analyze dataset"""
    mode = getattr(analyze_start, 'mode', 'analyze')

    folder_step = folder_step_path.get()
    folder_gallery = folder_gallery_path.get()
    folder_category = category_var.get()
    use_limit = use_file_limit.get()
    size_limit = file_limit_size.get()
    path_step = Path(folder_step)
    path_gallery = Path(folder_gallery)

    cache_connection = cache_init()

    # Check if selected category is the root gallery folder
    # The root folder is always the first item in the dropdown
    if folder_category == category_dropdown['values'][0]:
        path_items = path_gallery
    else:
        path_items = path_gallery / folder_category

    analyze_start.result_queue.put(('progress', 0, 1, "Getting files..."))

    # Get the items based on mode
    if mode == "reclassify":
        items = char_table.get_children()
    # For categorize mode, filter to only categorized items
    if mode == "categorize":
        items = char_table.get_children()
        items = [item for item in items 
                if (values := char_table.item(item, "values")) 
                and len(values) > 1 
                and values[1] 
                and values[1] != "Uncategorized"]
    if mode == "analyze":
        items = list(path_items.glob('*.png'))

    if not items:
        messagebox.showinfo("Nothing to analyze", "Nothing was found to be analysed.")
        return
    
    if mode == "analyze":
        # Get STEP files and filter out large files if setting is enabled
        step_files = list(chain(path_step.rglob('*.step'), path_step.rglob('*.stp')))
        if use_limit:
            analyze_start.result_queue.put(('progress', 0, 1, "Filtering files..."))
            step_files = [f for f in step_files if f.stat().st_size / 1024 <= size_limit]
        step_filenames = {file.stem: file for file in step_files}

        # Filter items to only those with corresponding STEP files
        items_to_process = []
        for item in items:
            part_name = item.stem
            if part_name in step_filenames:
                items_to_process.append((item, step_filenames[part_name]))
        
        total_items = len(items)
        skipped_count = total_items - len(items_to_process)
        success_count = 0
        categorized_count = 0
        total_tasks = len(items_to_process)
        completed = 0
        
        if not items_to_process:
            analyze_start.result_queue.put(('complete', 0, skipped_count, 0))
            return
        
        step_files_to_process = []
        for _item, step in items_to_process:
            if use_cache.get() is True:
                stem = step.stem
                step_mtime = float(step.stat().st_mtime)
                cached = cache_get_valid(cache_connection, stem, step_mtime)
                if cached:
                    cached.setdefault('filename', step.name)
                    analyze_start.result_queue.put(('result', cached))
                    success_count += 1
                    completed += 1
                    if cached.get('category') and cached['category'] != "Uncategorized":
                        categorized_count += 1
                    analyze_start.result_queue.put(('progress', skipped_count + completed, total_items, stem))
                else:
                    step_files_to_process.append(step)
            else:
                step_files_to_process.append(step)
        
        # Use process pool for parallel analysis
        max_workers = max(1, multiprocessing.cpu_count() - 1)
        print(f"Analyzing {len(step_files_to_process)} files with {max_workers} workers...")

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {executor.submit(analyze_worker_process, step): step for step in step_files_to_process}

            try:
                for fut in as_completed(futures):
                    # allow stop requests
                    if analyze_start.stop_flag.is_set():
                        # best-effort cancel remaining futures
                        for f in futures:
                            f.cancel()
                        break

                    step_file = futures[fut]
                    part_name = step_file.stem

                    try:
                        # guard per-task with a timeout to avoid indefinite hangs
                        result = fut.result(timeout=60)
                    except Exception as e:
                        print(f"Error analyzing {part_name}: {e}")
                        result = None

                    completed += 1

                    if result:
                        sf_mtime = float(step_file.stat().st_mtime)
                        wrote = cache_save(cache_connection, part_name, result, sf_mtime)
                        if not wrote:
                            print(f"Skipped cache write for {part_name} (no changes)")
                        elif wrote and use_cache.get() is False:
                            print(f"Updated cache for {part_name}")
                        analyze_start.result_queue.put(('result', result))
                        success_count += 1
                        if result.get('category') and result['category'] != "Uncategorized":
                            categorized_count += 1
                    else:
                        print(f"No characteristics for {part_name}")

                    # send a progress update for each completed task
                    analyze_start.result_queue.put(('progress', skipped_count + completed, total_items, part_name))

            finally:
                # Ensure all futures are cleaned up
                for f in futures:
                    try:
                        f.cancel()
                    except Exception:
                        pass
        
        analyze_start.result_queue.put(('complete', success_count, skipped_count, categorized_count))
 
    elif mode == "reclassify":
        hash_item_map, hash_stem_map = get_hash_map(cache_connection)        
        thread = threading.Thread(
            target=reclassify_worker,
            args=(hash_item_map, hash_stem_map, analyze_start.result_queue, analyze_start.stop_flag),
            daemon=True
        )
        thread.start()
        return   
        
    elif mode == "categorize":
        # Reclassify and categorize modes (single-threaded as before)
        total_items = len(items)
        success_count = 0
        skipped_count = 0
        categorized_count = 0
        completed = 0
        work_items= []
        
        for i, item in enumerate(items):
            if analyze_start.stop_flag.is_set():
                break

            part_name = char_table.set(item, "filename").split('.')[0]
            part = Path(folder_step) / f"{part_name}.step"
            # Get the modification time of the STEP file
            step_mtime = None
            if part.exists():
                step_mtime = float(part.stat().st_mtime)

            # Send progress update to GUI
            analyze_start.result_queue.put(('progress', i + 1, total_items, part_name))

            # Get the category
            values = char_table.item(item, "values")
            table_characteristics = char_reconstruct(values)
            category = table_characteristics.get('category', "Uncategorized")
            
            target_dir = path_gallery / category
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # Source file may be in a subfolder or in the root gallery
            if category_var.get() != path_gallery.name:
                source_file = path_gallery / category_var.get() / f"{part_name}.png"
            else:
                source_file = path_gallery / f"{part_name}.png"
            target_file = target_dir / source_file.name

            if target_file.exists() or category == "Uncategorized":
                skipped_count += 1
                continue

            try:
                shutil.move(str(source_file), str(target_file))
                print(f"Moved {source_file} to {target_file}")
                analyze_start.result_queue.put(('categorize_result', item))
                categorized_count += 1
                success_count += 1
            except Exception as e:
                print(f"Error moving {source_file}: {e}")
                skipped_count += 1

        analyze_start.result_queue.put(('complete', success_count, skipped_count, categorized_count))

def analyze_worker_process(step_file, generate_hashes=True):
    """Worker process for analyzing - runs in isolation"""
    try:
        result = get_characteristics(step_file, generate_hashes=generate_hashes)
        if result is None:
            return None
        characteristics, step_file_value = result

        return characteristics
    except Exception as e:
        print(f"Error analyzing {step_file}: {e}")
        return None

def browse_folder(folder_var):
    """Open folder selection dialog"""
    selected_folder_path = filedialog.askdirectory(title="Select Folder")
    if selected_folder_path:
        folder_var.set(selected_folder_path)
        settings_save()

def categorize_button_update(*args):
    """Enable/disable Categorize button based on char_table selection"""
    if char_table.get_children():
        categorize_button.configure(state="normal")
    else:
        categorize_button.configure(state="disabled")

def categorize_start():
    """Start categorization using the analyze function"""
    analyze_start("analyze", sort=True)

def char_reconstruct(values):
    """Reconstruct characteristics dictionary from table values, because TKinter converts everything to string"""
    characteristics = {}
    for i, col in enumerate(char_table["columns"]):
        if col == "index":
            continue
        if i < len(values) and values[i]:  # Only add non-empty values
            value = values[i]
            dtype = char_columns[col][3]
            characteristics[col] = (str(value).lower() == 'true') if dtype == bool else dtype(value)
    return characteristics

def char_table_clear():
    """Clear all items from the characteristics table"""
    children = char_table.get_children()
    if not children:
        return
    for item in children:
        char_table.delete(item)
    detached_categorized_items.clear()

def char_table_hide_categorized(*args):
    """Show/hide categorized entries in the char_table"""
    global detached_categorized_items
    
    if hide_categorized.get():
        print(f"Hiding categorized items")
        categorize_button.configure(state="disabled")
        for index, item in enumerate(list(char_table.get_children())):
            values = char_table.item(item, "values")
            if len(values) > 2:  # Make sure we have values
                category = char_table.set(item, "category")  # Category is the third column
                if category and category != "Uncategorized":
                    char_table.detach(item)
                    # Only add if not already in the list
                    if item not in detached_categorized_items:
                        detached_categorized_items.append((item, index))
    elif not hide_categorized.get() and detached_categorized_items:
        print(f"Reattaching {len(detached_categorized_items)} categorized items")
        for item, index in detached_categorized_items:
            current_items = char_table.get_children()
            insert_index = 0
            for current_item in current_items:
                if insert_index >= index:
                    break
                insert_index += 1
            char_table.reattach(item, '', insert_index if insert_index < len(current_items) else 'end')

        categorize_button.configure(state="normal")
        detached_categorized_items.clear()
    elif not hide_categorized.get():
        print("No categorized items to reattach")
        categorize_button.configure(state="normal")
        return
    
    # Update indices for visible items
    char_table_index_update()

    # Update summary stats for visible items only
    summary_stats_update()

def char_table_index_update():
    """Update the index column for all visible rows to show current row number"""
    for index, item in enumerate(char_table.get_children()):
        values = list(char_table.item(item, "values"))
        values[0] = index + 1  # index is the first column
        char_table.item(item, values=values)

def char_table_insert(characteristics):
    """Insert a new row into the characteristics table"""
    row_data = char_table_row_data(characteristics)
    next_index = len(char_table.get_children()) + 1
    row_data[0] = next_index  # Set the index column
    char_table.insert("", tk.END, values=row_data)

    # Detach right away if hiding categorized
    if hide_categorized.get():
        category = characteristics.get('category', "")
        if category and category != "Uncategorized":
            # detach the last inserted item
            children = char_table.get_children()
            if children:
                last = children[-1]
                char_table.detach(last)

# Update the char_table insertion and modification functions
def char_table_lookup(characteristics=None):
    """Refresh the characteristics table and update summary stats"""
    # Clear existing items
    char_table_clear()

    if characteristics is None:
        summary_stats_update()
        return
    
    char_table_insert(characteristics)

def char_table_row_data(characteristics):
    """Get row data for characteristics dictionary"""
    row_data = []
    for col in char_table["columns"]:
        if col == "index":
            row_data.append("")  # Index will be set later
        elif col in characteristics:
            value = characteristics[col]
            if isinstance(value, float):
                row_data.append(f"{value:.2f}")
            else:
                row_data.append(value)
        else:
            row_data.append("")  # Empty for missing columns
    return row_data

def char_table_row_update(item, characteristics):
    """Update an existing row in the characteristics table"""
    row_data = char_table_row_data(characteristics)
    char_table.item(item, values=row_data)
    if hide_categorized.get():
        char_table_hide_categorized()

def char_table_row_color():
    """Color rows based on instance count and store as tags"""
    if not color_instances.get():
        # Clear all tags if coloring is disabled
        for item in char_table.get_children():
            char_table.item(item, tags=())
        return
    
    # Build a map of characteristics to items
    char_to_items = {}
    color_tags = {}
    for item in char_table.get_children():
        values = char_table.item(item, "values")
        chars = char_reconstruct(values)
        chars_compare = {k: v for k, v in chars.items() if k not in excluded_columns}
        
        char_key = tuple(sorted(chars_compare.items()))
        char_to_items.setdefault(char_key, []).append(item)
    
    # Apply tags based on instance count - tags are stored with each row
    for char_key, items in char_to_items.items():
        if len(items) == 1:
            # Single instance - no coloring
            for item in items:
                char_table.item(item, tags=())
        else:
            # Multiple instances - generate consistent color
            first_item_values = char_table.item(items[0], "values")
            color_hash_index = list(char_table["columns"]).index("color_hash")
            color_hex = first_item_values[color_hash_index]
            tag_name = f"color_{color_hex[1:]}"  # Remove # from hex
            
            # Create the tag if it doesn't exist
            if tag_name not in color_tags:
                char_table.tag_configure(tag_name, background=color_hex)
                color_tags[tag_name] = True
            
            # Apply the color tag to all items with these characteristics
            for item in items:
                char_table.item(item, tags=(tag_name,))

def char_table_sort_column(col, reverse):
    """Sort char_table by the specified column"""
    if col == "color_hash":
        # build counts for each color value
        counts = {}
        children = list(char_table.get_children(''))
        for child in children:
            val = char_table.set(child, "color_hash") or ""
            counts[val] = counts.get(val, 0) + 1

        # build list of (count, secondary_key, item)
        # secondary_key keeps ordering stable (use filename)
        items = []
        for child in children:
            count = counts.get(char_table.set(child, "color_hash") or "", 0)
            color = (char_table.set(child, "color_hash") or "").lower()
            secondary = char_table.set(child, "filename") or ""
            items.append((count, color, secondary.lower(), child))

        # Default first-click (reverse=False) -> most frequent first
        items.sort(key=lambda x: (x[0], x[1], x[2]), reverse=not reverse)

        # Rearrange items
        for index, (_count, _color, _sec, child) in enumerate(items):
            char_table.move(child, '', index)

        # Update indices and headings
        char_table_index_update()
        for column in char_table["columns"]:
            heading_text = char_columns[column][0]
            if column == col:
                heading_text += " ↓" if reverse else " ↑"
            char_table.heading(column, text=heading_text)
        # Store sort state
        char_table.heading(col, command=lambda: char_table_sort_column(col, not reverse))
        return

    # Get all items with their values
    items = [(char_table.set(child, col), child) for child in char_table.get_children('')]
    
    # Determine data type and sort accordingly
    dtype = char_columns[col][3]

    if dtype in (int, float):
        # Sort numerically - handle empty values
        items.sort(key=lambda x: float(x[0]) if x[0] and x[0] != "" else float('-inf'), reverse=reverse)
    elif dtype == bool:
        # Sort booleans - True comes first when not reversed
        items.sort(key=lambda x: str(x[0]).lower() == 'true' if x[0] else False, reverse=reverse)
    else:
        # Sort strings (case-insensitive)
        items.sort(key=lambda x: str(x[0]).lower() if x[0] else "", reverse=reverse)
    
    # Rearrange items in sorted positions
    for index, (val, child) in enumerate(items):
        char_table.move(child, '', index)

    # Update all row indices after sorting
    char_table_index_update()
    
    # Update column heading to show sort direction
    for column in char_table["columns"]:
        heading_text = char_columns[column][0]
        if column == col:
            # Add arrow to indicate sort direction
            heading_text += " ↓" if reverse else " ↑"
        char_table.heading(column, text=heading_text)
    
    # Store sort state for next click
    char_table.heading(col, command=lambda: char_table_sort_column(col, not reverse))

def copy_selected_filename(event=None):
    """Copy filename of the currently selected item in char_table to the clipboard"""
    sel = char_table.selection()
    if not sel:
        return
    item = sel[0]
    filename = char_table.set(item, "filename") or ""
    if not filename:
        return
    stem = get_item_stem(item)
    try:
        root.clipboard_clear()
        root.clipboard_append(stem)
        # Ensure clipboard is set immediately (helps on Windows)
        root.update()
        print(f"Copied to clipboard: {stem}")
    except Exception as e:
        print(f"Clipboard copy failed: {e}")

def csv_export():
    step_folder = folder_step_path.get()
    gallery_folder = folder_gallery_path.get()
    if not step_folder or not gallery_folder:
        messagebox.showerror("Export Error", "Please make sure you have both the STEP and Gallery folder set before exporting.")
        return
    
    csv_file = Path(r"output/secondary_data.csv")
    # check if path is legit and exists BEFORE trying to export (save you from waiting HOURS just to get an error - youre welcome ;)
    csv_file = Path(__file__).parent / "output" / "secondary_data.csv"
    try:
        csv_file.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_file, "a") as file:
            pass
        print(f"Initializing export process for {csv_file.resolve()}")
    except Exception as ex:
        messagebox.showerror("Export Error", f"Could not create or access output file with path:\n{csv_file}\n\nError: {ex}")
        return

    thread = threading.Thread(
        target=csv_export_worker,
        args=(step_folder, gallery_folder, csv_file),
        daemon=True
    )
    thread.start()

def csv_export_worker(step_folder, gallery_folder, csv_file):
    """Worker thread for CSV export"""
    use_limit = use_file_limit.get()
    size_limit = file_limit_size.get()
    path_step = Path(step_folder)
    path_gallery = Path(gallery_folder)
    generate_hashes = False

    # Update UI state
    analyze_button.configure(state="disabled")
    categorize_button.configure(state="disabled")
    reclassify_button.configure(state="disabled")
    generate_csv_button.configure(state="disabled")
    generate_gallery_button.configure(state="disabled")
    progress_label.config(text="Exporting CSV...")
    progress_bar['value'] = 0

    try:
        # Get STEP files
        step_files = list(chain(path_step.rglob('*.step'), path_step.rglob('*.stp')))
        if use_limit:
            step_files = [f for f in step_files if f.stat().st_size / 1024 <= size_limit]
        
        total_files = len(step_files)
        progress_bar['maximum'] = total_files

        results_to_write = []

        # Use process pool for parallel analysis
        max_workers = max(1, multiprocessing.cpu_count() - 1)
        
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(analyze_worker_process, step, generate_hashes): step for step in step_files}
            
            for i, fut in enumerate(as_completed(futures)):
                step_file = futures[fut]
                try:
                    result = fut.result(timeout=60)
                    if result:
                        part_name = step_file.stem
                        found_img = next((path_gallery.rglob(f"{part_name}.png")), None)

                        # If the image exists and it's not in the root gallery, determine its category based on its parent folder
                        if found_img and found_img.parent.resolve() != path_gallery.resolve():
                            result['category'] = found_img.parent.name
                        
                        results_to_write.append(result)
                except Exception as e:
                    print(f"Error processing {step_file}: {e}")
                
                # Update progress
                progress_bar['value'] = i + 1
                progress_label.config(text=f"Exporting {i + 1}/{total_files}: {step_file.name}")

        # Write to CSV
        if results_to_write:
            # Use keys from the first result as headers
            fieldnames = list(results_to_write[0].keys())

            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results_to_write)

        progress_label.config(text=f"Successfully exported {total_files} items to {csv_file}.")

    except Exception as e:
        print(f"Error exporting CSV: {e}")
    
    finally:
        # Restore UI state
        analyze_button.configure(state="normal")
        categorize_button.configure(state="normal")
        reclassify_button_update()
        generate_csv_button.configure(state="normal")
        generate_gallery_button.configure(state="normal")
        progress_label.config(text="Ready")
        progress_bar['value'] = 0

def gallery_generate_start(*args):
    """Start gallery generation in background thread"""
    if hasattr(gallery_generate_start, 'running') and gallery_generate_start.running:
        gallery_generate_stop()
        return
    
    gallery_generate_start.running = True
    
    generate_gallery_button.configure(text="Stop", state="normal")
    generate_csv_button.configure(state="disabled")
    analyze_button.configure(state="disabled")
    categorize_button.configure(state="disabled")
    reclassify_button.configure(state="disabled")
    hide_categorized_check.configure(state="disabled")
    file_limit_check.configure(state="disabled")
    color_instances_check.configure(state="disabled")
    
    # Create queue for communication
    gallery_generate_start.result_queue = queue.Queue()
    gallery_generate_start.stop_flag = threading.Event()
    
    # Start background thread
    thread = threading.Thread(target=gallery_generate_worker, daemon=True)
    thread.start()
    
    # Start GUI update loop
    gallery_generate_update()

def gallery_generate_stop():
    """Stop gallery generation"""
    if hasattr(gallery_generate_start, 'stop_flag'):
        gallery_generate_start.stop_flag.set()
        generate_gallery_button.configure(text="Generate Gallery")
        progress_label.config(text="Stopped")
        gallery_generate_start.running = False
        analyze_button_update()
        categorize_button_update()
        reclassify_button_update()

def gallery_generate_update():
    """Check for results from gallery generation thread"""
    try:
        while True:
            msg_type, *args = gallery_generate_start.result_queue.get_nowait()
            
            if msg_type == 'progress':
                current, total, filename = args
                progress_bar['value'] = current
                progress_bar['maximum'] = total
                progress_label.config(text=f"Generating gallery {current}/{total}: {filename}")
                hide_categorized_check.configure(state="disabled")
                file_limit_check.configure(state="disabled")
                color_instances_check.configure(state="disabled")
                
            elif msg_type == 'complete':
                success_count, skipped_count = args
                progress_bar['value'] = 0
                progress_label.config(text=f"Done: {success_count} generated, {skipped_count} skipped")
                generate_gallery_button.configure(text="Generate Gallery")
                generate_csv_button.configure(state="normal")
                analyze_button_update()
                categorize_button_update()
                reclassify_button_update()
                hide_categorized_check.configure(state="normal")
                file_limit_check.configure(state="normal")
                color_instances_check.configure(state="normal")
                gallery_generate_start.running = False
                return
                
            elif msg_type == 'error':
                error_msg = args[0]
                messagebox.showerror("Gallery Error", f"Error generating gallery: {error_msg}")
                generate_gallery_button.configure(text="Generate Gallery")
                generate_csv_button.configure(state="normal")
                analyze_button_update()
                categorize_button_update()
                reclassify_button_update()
                hide_categorized_check.configure(state="normal")
                file_limit_check.configure(state="normal")
                color_instances_check.configure(state="normal")
                gallery_generate_start.running = False
                return
                
    except queue.Empty:
        pass
    
    # Schedule next check if still running
    if gallery_generate_start.running:
        root.after(50, gallery_generate_update)

def gallery_generate_worker():
    """Worker thread for gallery generation"""
    folder_step = folder_step_path.get()
    folder_gallery = folder_gallery_path.get()
    use_limit = use_file_limit.get()
    limit_size = file_limit_size.get()
    disp_axis = disp_axis_var.get()
    no_background = no_background_var.get()
    
    path_step = Path(folder_step)
    path_gallery = Path(folder_gallery)
    gallery_generate_start.result_queue.put(('progress', 0, 1, "Getting files..."))
    
    # Get STEP files
    step_files = list(chain(path_step.rglob('*.step'), path_step.rglob('*.stp')))
    
    # Filter by file size if enabled
    if use_limit:
        gallery_generate_start.result_queue.put(('progress', 0, 1, "Filtering files..."))
        step_files = [f for f in step_files if f.stat().st_size / 1024 <= limit_size]
    
    if not step_files:
        gallery_generate_start.result_queue.put(('error', "No STEP files found"))
        return
    
    # Filter out files that already have images
    files_to_process = []
    for step_file in step_files:
        output_path = path_gallery / f"{step_file.stem}.png"
        if not output_path.exists():
            files_to_process.append(step_file)
    
    total_files = len(step_files)
    skipped_count = total_files - len(files_to_process)
    success_count = 0
    error_count = 0

    if not files_to_process:
        gallery_generate_start.result_queue.put(('complete', 0, skipped_count))
        return
    
    max_workers = max(1, multiprocessing.cpu_count() - 1)

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(gallery_render_worker, step_file, path_gallery, disp_axis, no_background): step_file for step_file in files_to_process}
        completed = 0

        for fut in as_completed(futures):
            # allow stop requests
            if gallery_generate_start.stop_flag.is_set():
                # best-effort cancel remaining futures
                for f in futures:
                    f.cancel()
                break

            step_file = futures[fut]
            try:
                # per-task timeout guards against hangs
                success = fut.result(timeout=30)
            except Exception as e:
                error_count += 1
                print(f"Error rendering {step_file.name}: {e}")
                success = False

            if success:
                success_count += 1
                print(f"Generated preview for {step_file.name}")
            else:
                # already counted errors above
                if not isinstance(success, bool):
                    error_count += 1
                print(f"Error rendering {step_file.name}")

            completed += 1
            gallery_generate_start.result_queue.put(
                ('progress', skipped_count + completed, total_files, step_file.name)
            )
    
    
    gallery_generate_start.result_queue.put(('complete', success_count, skipped_count))

def gallery_render_worker(step_file, output_dir, disp_axis=True, no_background=False):
    """Worker process for rendering - runs in isolation"""
    try:
        output_path = output_dir / f"{step_file.stem}.png"
        bbox = BoundingBox(step_file, output_dir=output_dir)
        bbox.render(filename=output_path.name, disp_axis=disp_axis, no_background=no_background)
        return True
    except Exception as e:
        print(f"Error rendering {step_file}: {e}")
        return False

def gallery_button_update(*args):
    """Enable/disable Generate Gallery button"""
    folder_step = folder_step_path.get()
    folder_gallery = folder_gallery_path.get()
    
    if folder_step and folder_gallery:
        generate_gallery_button.configure(state="normal")
    else:
        generate_gallery_button.configure(state="disabled")

def get_hash_map(cache_connection = None):
    """
    Get a map of item hashes to char_table items for quick lookup
    Arguments:
        cache_connection: Optional cache connection to use
    Returns:
        dict: Map of hash -> char_table item
    """
    hash_item_map = {}
    hash_stem_map = {}
    
    if cache_connection is None:
        cache_connection = cache_init()
    
    for item in char_table.get_children():
        stem = get_item_stem(item)
        try:
            cached_item = cache_get_row(cache_connection, stem)
        except Exception:
            cached_item = None

        # cached_item expected to be (data_dict, mtime) or None
        if not cached_item:
            continue
        cached_data, _ = cached_item
        if not cached_data:
            continue

        cached_hash = cached_data.get('hash', None)
        if cached_hash:
            hash_item_map.setdefault(cached_hash, []).append(item)
            hash_stem_map.setdefault(cached_hash, []).append(stem)
    return hash_item_map, hash_stem_map


def get_header_tooltip(col_name):
    if col_name in char_columns:
        return f"{char_columns[col_name][4]}"
    return ""

def get_instances(*args):
    """Count rows with the same characteristics as the selected item and update an Instances label."""
    selection = char_table.selection()
    if not selection:
        instance_label_count.set("0")
        return

    # Reconstruct characteristics of selected row (exclude filename from comparison)
    sel_item = selection[0]
    sel_values = char_table.item(sel_item, "values")
    sel_chars = char_reconstruct(sel_values)
    sel_compare = {k: v for k, v in sel_chars.items() if k not in excluded_columns}

    # Count matching rows
    count = 0
    for item in char_table.get_children():
        vals = char_table.item(item, "values")
        chars = char_reconstruct(vals)
        chars_compare = {k: v for k, v in chars.items() if k not in excluded_columns}

        if chars_compare == sel_compare:
            count += 1

    instance_label_count.set(f"{count}")

def get_item_filename(item):
    """Get the filename for a given char_table item"""
    return char_table.set(item, "filename") or ""

def get_item_stem(item):
    """Get the stem (filename without extension) for a given char_table item"""
    filename = char_table.set(item, "filename")
    return filename.split('.')[0] if filename else ""

def get_most_common_values(data, col):
    """Get the most common value for a given key in a list of dictionaries"""
    values = data[col]
    
    # Count occurrences of each value
    value_counts = {}
    for value in values:
        value_counts[value] = value_counts.get(value, 0) + 1
    
    # Find the most common value
    most_common_value = max(value_counts, key=value_counts.get)
    dtype = char_columns[col][3]
    # Format according to column type
    return (str(most_common_value).lower() == 'true') if dtype == bool else dtype(most_common_value)



def lookup(*args):
    """Lookup in dataset"""
    # Get the part name and paths
    part_name_value = part_name.get().strip()
    gallery_path_value = folder_gallery_path.get()
    step_path_value = folder_step_path.get()

    found_files = []
    selected_file = None

    # Check if the part image exists directly in root
    direct_file = Path(gallery_path_value) / (part_name_value + ".png")
    if direct_file.exists():
        selected_file = direct_file
    else:
        found_files = list(Path(gallery_path_value).rglob(part_name_value + ".png"))

    # Check if STEP file exists directly
    step_file = Path(step_path_value) / (part_name_value + ".step")
    if not step_file.exists():
        step_files = list(chain(Path(step_path_value).rglob('*.step'), Path(step_path_value).rglob('*.stp')))
        # Filter out files based on size if setting is enabled
        if settings.get("use_file_limit"):
            step_files = [f for f in step_files if f.stat().st_size / 1024 <= settings.get("file_limit_size")]
        step_filenames = {file.name: file for file in step_files}

    if not found_files and selected_file is None:
        messagebox.showinfo("Not Found", f"{part_name_value} not found.")
        return
    elif len(found_files) > 1:
        # Multiple files found - let user choose
        file_list = "\n".join([f"{i+1}. {file}" for i, file in enumerate(found_files)])
        choice = simpledialog.askinteger(
            "Multiple Files Found", 
            f"Found {len(found_files)} files:\n\n{file_list}\n\nEnter number (1-{len(found_files)}):",
            minvalue=1, 
            maxvalue=len(found_files)
        )
        
        if choice:
            selected_file = found_files[choice-1]
            print(f"Selected file: {selected_file}")
            return selected_file
        else:
            print("No file selected")
            return None
    elif selected_file is None and len(found_files) == 1:
        selected_file = found_files[0]
        print(f"Found file: {selected_file}")

    try:
        gallery_root_name = Path(gallery_path_value).name
        image_parent_name = selected_file.parent.name
        category = image_parent_name if image_parent_name != gallery_root_name else None

        print(f"Gallery root: {gallery_root_name}, Image parent: {image_parent_name}, Category: {category}")

        # Get characteristics using the selected image file
        result = get_characteristics(step_file, category=category)
        
        if result is None:
            messagebox.showinfo("Error", f"Could not find corresponding STEP file for {part_name_value}")
            return
            
        characteristics, step_file = result

        char_table_lookup(characteristics)
        # Select the first item in char_table if present
        children = char_table.get_children()
        if children:
            first = children[0]
            char_table.selection_set(first)
            char_table.focus(first)
            char_table.see(first)
            rule_button_update()

    except Exception as e:
        messagebox.showerror("Error", f"Error loading characteristics: {str(e)}")
        print(f"Error processing {selected_file}: {e}")

def lookup_paste(event=None):
    """Paste from clipboard and lookup"""
    try:
        clipboard_content = root.clipboard_get().strip()
        if not isinstance(clipboard_content, str):
            return
        part_name_entry.focus_set()
        part_name_entry.delete(0, tk.END)
        part_name_entry.insert(0, clipboard_content)
        lookup(event)
    except Exception as e:
        print(f"Clipboard paste failed: {e}")
    
def lookup_button_update(*args):
    """Enable/disable Add Rule button based on a correctly set up dataset"""
    part_name_value = part_name_entry.get().strip()
    folder_gallery_path_value = folder_gallery_path.get()
    folder_step_path_value = folder_step_path.get()

    if part_name_value and folder_gallery_path_value and folder_step_path_value:
        lookup_button.configure(state="normal")
    else:
        lookup_button.configure(state="disabled")

def on_category_selected(event):
    """Called when user selects a category from dropdown"""
    category_displayed = category_display_var.get()
    category_actual = _display_to_category_map.get(category_displayed, category_displayed)
    category_var.set(category_actual)
    
    settings_save()
    analyze_button_update()

def on_file_limit_toggle():
    """Called when user toggles file limit checkbox"""
    toggle_file_limit()
    settings_save()

def on_part_name_enter(event):
    """Handle Enter in part name entry with validation"""
    if part_name.get().strip() and folder_gallery_path.get() and folder_step_path.get():
        lookup(event)
    else:
        pass

def on_rule_name_enter(event):
    """Handle Enter in rule name entry with validation"""
    if char_table.selection() and rule_name_new.get().strip():
        rule_create(event)
    else:
        pass

def populate_category_dropdown(*args):
    """Populate category dropdown with subfolders from gallery path"""
    global _display_to_category_map, _category_to_display_map
    gallery_path = folder_gallery_path.get()
    
    if not gallery_path:
        category_dropdown['values'] = []
        category_var.set("")
        category_display_var.set("")
        _display_to_category_map.clear()
        _category_to_display_map.clear()
        return
    
    gallery_dir = Path(gallery_path)
    if not gallery_dir.exists() or not gallery_dir.is_dir():
        category_dropdown['values'] = []
        category_var.set("")
        category_display_var.set("")
        _display_to_category_map.clear()
        _category_to_display_map.clear()
        return
    
    # Get all subdirectories
    subfolders = [d for d in gallery_dir.iterdir() if d.is_dir() and d.name not in ('IGNORE')]
    subfolders.sort(key=lambda d: d.name)

    # Clear existing options
    display_options = []
    _display_to_category_map.clear()
    _category_to_display_map.clear()

    # Build display options with item counts for the dropdown
    for folder in subfolders:
        folder_items = len(list(folder.glob('*.png')))
        folder_display = f"{folder.name} ({folder_items})"
        display_options.append(folder_display)
        _display_to_category_map[folder_display] = folder.name
        _category_to_display_map[folder.name] = folder_display

    # Add root gallery folder as first option
    root_folder_name = gallery_dir.name
    all_options = [root_folder_name] + display_options
    
    # Update dropdown
    category_dropdown['values'] = all_options
    
    # Temporarily unbind the event to prevent triggering settings_save
    category_dropdown.unbind("<<ComboboxSelected>>")
    
    # Set first value to the saved setting or root folder
    category_var.set(settings.get("category", root_folder_name))
    category_display_var.set(_category_to_display_map.get(category_var.get(), root_folder_name))
    
    # Re-bind the event
    category_dropdown.bind("<<ComboboxSelected>>", on_category_selected)
    
    print(f"Found {len(subfolders)} category folders: {subfolders}")

def reclassify_button_update(*args):
    """Enable/disable Reclassify button based on char_table content"""
    items = char_table.get_children()
    
    if items and not (hasattr(analyze_start, 'running') and analyze_start.running):
        reclassify_button.configure(state="normal", text="Reclassify")
    else:
        reclassify_button.configure(state="disabled")

def reclassify_worker(hash_item_map, hash_stem_map, result_queue, stop_flag):
    """Iterate rule hashes and update only matching stems (runs in background thread)."""
    print("Reclassify worker started")
    conn = cache_init()   # new connection inside thread
    # Build tasks pairing stems (strings) with items (Treeview ids)
    tasks = []
    for hash, items in hash_item_map.items():
        if hash in rules_category_map:
            stems = hash_stem_map.get(hash, [])
            tasks.append((hash, rules_category_map[hash], stems, items))

    total = sum(len(stems) for _, _, stems, _ in tasks)
    done = 0
    changed = 0
    print(f"Checking {[(h, cat, stems) for h, cat, stems, _ in tasks]}")

    for hash, new_cat, stems, items in tasks:
        # iterate by index so each stem lines up with its corresponding item id
        for i, stem in enumerate(stems):
            if stop_flag.is_set():
                break
            item = items[i] if i < len(items) else None

            # stem is the string used by the cache; pass that to cache_get_valid
            row = cache_get_row(conn, stem)
            if not row:
                done += 1
                result_queue.put(('progress', done, total, stem))
                continue

            cached, mtime = row
            if cached is None:
                done += 1
                result_queue.put(('progress', done, total, stem))
                continue
            
            old = cached.get('category', 'Uncategorized')
            if old != new_cat:
                cached['category'] = new_cat
                try:
                    cache_save(conn, stem, cached, None)
                except Exception as e:
                    print(f"cache_save failed for {stem}: {e}")
                if item:
                    result_queue.put(('reclassify_result', item, cached))
                changed += 1

            done += 1
            result_queue.put(('progress', done, total, stem))

    result_queue.put(('complete', done, 0, changed))

def rule_button_update(*args):
    """Enable/disable Add Rule button based on char_table selection"""
    selection = char_table.selection()
    rule_name = rule_name_new.get().strip()

    if selection and rule_name:
        add_rule_button.configure(state="normal")
    else:
        add_rule_button.configure(state="disabled")

def rule_classify(hash):
    """
    Classify a part using saved rules.
    Returns (category, confidence_score) or (None, 0) if no match found.
    """   
    if not hash:
        print("No hash found in characteristics")
        return None

    if hash in rules_category_map:
        category = rules_category_map[hash]
        print(f"Direct hash match found: {hash} -> {category}")
        return category
            
    return None

def rule_create(*args):
    """Create a new rule from selected characteristics"""
    # Get the selected item's data
    item = char_table.selection()[0]
    values = char_table.item(item, "values")
    filename = char_table.set(item, "filename")
    stem = get_item_stem(item)
    file_path = Path(folder_step_path.get()) / filename
    print(f"Selected item values: {values}")

    cache_connection = cache_init()
    hash_item_map, hash_stem_map = get_hash_map(cache_connection)

    # find hash for this stem by searching the hash->stems map
    found_hash = next((h for h, stems in hash_stem_map.items() if stem in stems), None)

    # prefer cached full characteristics if available
    cached_row = cache_get_row(cache_connection, stem)
    if not found_hash and cached_row and cached_row[0]:
        found_hash = cached_row[0].get("hash")

    if not found_hash:
        chars = get_characteristics(file_path)[0]
        found_hash = chars.get("hash")
        cached_row = (chars, None)

    rule_characteristics = {"filename": filename, "hash": found_hash}
    print(f"Creating rule for characteristics: {rule_characteristics}")
    rule_save(rule_characteristics)

    # Update the selected item's category using rule_classify
    category = rule_classify(found_hash)
    if category:
        full_chars = (cached_row[0] if cached_row and cached_row[0] else get_characteristics(file_path)[0])
        full_chars['category'] = category
        char_table_row_update(item, full_chars)

    rule_populate()
    rule_name_new.set("")

def rule_delete(*args):
    """Delete selected rule"""
    selected_item = rule_table.selection()
    if not selected_item:
        messagebox.showinfo("No Selection", "No rule selected to delete.")
        return

    rule_name = rule_table.item(selected_item, "values")[0]
    rule_file = RULES_DIR / f"{rule_name}.json"

    if rule_file.exists():
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the rule '{rule_name}'?"):
            try:
                rule_file.unlink()

                # Remove all hashes for this category from rules_category_map
                for k in list(rules_category_map.keys()):
                    if rules_category_map.get(k) == rule_name:
                        del rules_category_map[k]
                        print(f"Removed hash mapping: {k[:16]}... -> {rule_name}")

                # Remove from cache by trying to reload
                rule_loadone(rule_name)
                print(f"Deleted rule file: {rule_file}")
                rule_populate()
            except Exception as e:
                messagebox.showerror("Error", f"Error deleting rule file: {e}")
    else:
        messagebox.showinfo("File Not Found", f"Rule file '{rule_file}' does not exist.")

def rule_loadall(recalculate=False):
    """Load all category rules from the rules directory."""
    global rules_category_map
    rules_category_map = {}

    for rule_file in RULES_DIR.glob("*.json"):
        with open(rule_file, 'r') as f:
            rule_data = json.load(f)
            category = rule_data.get("category", rule_file.stem)
            for example in rule_data.get("examples", []):
                char_hash = example.get("characteristics", {}).get("hash")
                rules_category_map[char_hash] = category
                
def rule_loadone(category):
    """Load or reload a single rule file into cache."""
    global rules_category_map
    
    rule_file = RULES_DIR / f"{category}.json"
    if not rule_file.exists():
        # Remove all hash mappings that point to this category
        for k in list(rules_category_map.keys()):
            if rules_category_map.get(k) == category:
                del rules_category_map[k]
        return None
    
    try:
        with open(rule_file, 'r') as f:
            rule_data = json.load(f)

            # Remove old hash mappings for this category first
            for k in list(rules_category_map.keys()):
                if rules_category_map.get(k) == category:
                    del rules_category_map[k]

            for example in rule_data.get("examples", []):
                char_hash = example.get("characteristics", {}).get("hash")
                if char_hash:
                    rules_category_map[char_hash] = category
            return rule_data
    except Exception as e:
        print(f"Error loading rule file {rule_file.name}: {e}")
        return None

def rule_on_double_click(event):
    """Handle double-click on rule table to create a new rule."""
    selected_rule = rule_table.selection()
    if not selected_rule:
        return
    
    # Check if there's a selected item in characteristics table
    selected_char = char_table.selection()
    if not selected_char:
        messagebox.showinfo("No Selection", "Please select a characteristic row first.")
        return

    rule_name = rule_table.item(selected_rule, "values")[0]

    # Get the selected item's data
    item = char_table.selection()[0]
    filename = char_table.set(item, "filename")
    stem = get_item_stem(item)
    values = char_table.item(item, "values")

    file_path = Path(folder_step_path.get()) / filename
    print(f"Selected item values: {values}")

    cache_connection = cache_init()
    hash_item_map, hash_stem_map = get_hash_map(cache_connection)

    found_hash = next((h for h, stems in hash_stem_map.items() if stem in stems), None)

    cached_row = cache_get_row(cache_connection, stem)
    if not found_hash and cached_row and cached_row[0]:
        found_hash = cached_row[0].get("hash")

    if not found_hash:
        chars = get_characteristics(file_path)[0]
        found_hash = chars.get("hash")
        cached_row = (chars, None)

    # Update the visible category in the table
    updated_values = list(values)
    category_index = list(char_table["columns"]).index("category")
    updated_values[category_index] = rule_name
    char_table.item(item, values=updated_values)

    # Temporarily set rule_name_new to the selected rule name for rule_save to use
    original_rule_name = rule_name_new.get()
    rule_name_new.set(rule_name)

    rule_characteristics = {"filename": filename, "hash": found_hash}

    print(f"Creating rule for hash: {found_hash}")
    rule_save(rule_characteristics)

    # Restore the original rule_name_new value
    rule_name_new.set(original_rule_name)

    rule_populate()
    rule_name_new.set("")

def rule_on_right_click(event=None):
    """Right-click a rule -> move selected char_table items into that rule's gallery folder.
    Does NOT add the rule example to disk; updates cache and UI only."""
    global supress_preview
    sel_rule = rule_table.selection()
    if not sel_rule:
        return
    rule_name = rule_table.item(sel_rule[0], "values")[0]

    sel_items = char_table.selection()
    if not sel_items:
        messagebox.showinfo("No Selection", "Please select one or more rows in the Characteristics table to move.")
        return

    gallery_root = Path(folder_gallery_path.get())
    if not gallery_root.exists():
        messagebox.showerror("Gallery Not Found", f"Gallery folder does not exist: {gallery_root}")
        return

    supress_preview = True
    conn = cache_init()
    moved = 0
    skipped = 0
    lowest_index = None
    moved_hashes = []

    for item in sel_items:
        stem = get_item_stem(item)
        # Determine source path based on current category
        if category_var.get() != gallery_root.name:
            src_path = gallery_root / category_var.get() / f"{stem}.png"
        else:
            src_path = list(gallery_root.rglob(f"{stem}.png"))
            src_path = src_path[0] if src_path else None

        if not src_path:
            print(f"No gallery image found for {stem}; skipping")
            skipped += 1
            continue

        # Get the lowest index of the selected items
        if lowest_index is None:
            lowest_index = char_table.index(item)
        else:
            lowest_index = min(lowest_index, char_table.index(item))

        target_dir = gallery_root / rule_name
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_path = target_dir / src_path.name

        # avoid overwriting existing file
        if dest_path.exists():
            print(f"Target file already exists, skipping: {dest_path}")
            skipped += 1
            continue

        try:
            shutil.move(str(src_path), str(dest_path))
        except Exception as e:
            messagebox.showerror("Move Error", f"Failed to move {src_path} -> {dest_path}: {e}")
            skipped += 1
            continue

        # update cache category if present
        try:
            row = cache_get_row(conn, stem)
        except Exception:
            row = None
        if row and row[0]:
            cached, mtime = row
            cached['category'] = rule_name
            items_hash = cached.get('hash')
            item_category = char_table.set(item, "category")
            if items_hash and items_hash in rules_category_map or item_category != rule_name and item_category != "Uncategorized":
                moved_hashes.append(items_hash)
            try:
                cache_save(conn, stem, cached, mtime)
            except Exception as e:
                print(f"cache_save failed for {stem}: {e}")

        # Remove the item from the table since it was moved
        char_table.delete(item)
        moved += 1

    # Remove moved hashes from rules_category_map and rule files

    if len(moved_hashes) > 0:
        for hash_val in moved_hashes:
            if hash_val in rules_category_map:
                rule_remove_hash(hash_val)
        # Only prompt if any were removed from rules
        messagebox.showinfo("Hashes Removed", f"Removed {len(moved_hashes)} hash mappings from rules.")

    if moved or skipped:
        supress_preview = False
        # Set the lowest index
        target_idx = lowest_index - 1 if lowest_index > 0 else 0
        # Make sure there are still items left and set the selection
        children = list(char_table.get_children()) or []
        if children and target_idx < len(children):
            target = children[target_idx]
            char_table.selection_set(target)
            char_table.focus(target)
            char_table.see(target)
        # refresh dependent UI state
        summary_stats_update()
        char_table_row_color()

def rule_populate():
    """Load rules and populate the rule table"""
    # Clear existing items
    for item in rule_table.get_children():
        rule_table.delete(item)
    
    # Load rules and populate table
    rules = rule_loadall()

    # Count the matches for each category
    match_counts = {}
    for item in char_table.get_children():
        cat = char_table.set(item, "category")
        if cat and cat != "Uncategorized":
            match_counts[cat] = match_counts.get(cat, 0) + 1

    # Read rule files to get example counts
    for rule_file in RULES_DIR.glob("*.json"):
        with open(rule_file, 'r') as f:
            rule_data = json.load(f)
            category = rule_data.get("category", rule_file.stem)
            examples_count = len(rule_data.get("examples", []))
            matches = match_counts.get(category, 0)
            rule_table.insert("", tk.END, values=(category, examples_count, matches))

def rule_remove_hash(hash_val):
    """Remove a hash from the rules_category_map and update rule files."""
    global rules_category_map
    if hash_val not in rules_category_map:
        return

    category = rules_category_map[hash_val]
    rule_file = RULES_DIR / f"{category}.json"

    # Remove hash from mapping
    del rules_category_map[hash_val]
    print(f"Removed hash mapping: {hash_val[:16]}... -> {category}")

    # Update rule file to remove examples with this hash
    if rule_file.exists():
        try:
            with open(rule_file, 'r', encoding='utf-8') as f:
                rules = json.load(f)
            
            # Remove examples with matching hash
            examples = rules.get("examples", [])
            rules["examples"] = [
                ex for ex in examples
                if ex.get("characteristics", {}).get("hash") != hash_val
            ]
            
            with open(rule_file, 'w', encoding='utf-8') as f:
                json.dump(rules, f, indent=2)
            
            print(f"Removed hash {hash_val}... from '{category}'")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update rule file: {e}")

def rule_save(characteristics):
    """Save a part's characteristics as a rule for a category."""
    global rules_category_map
    extracted_part_name = characteristics.get('filename', 'unknown')
    extracted_part_name = extracted_part_name.split('.')[0] if '.' in extracted_part_name else extracted_part_name
    category = rule_name_new.get()
    rule_file = RULES_DIR / f"{category}.json"
    hash_val = characteristics.get('hash')

    # Check if hash exists in a different category
    existing_category = rules_category_map.get(hash_val)
    if existing_category and existing_category != category:
        response = messagebox.askyesno(
            "Hash Conflict",
            f"This hash is already used in rule '{existing_category}'.\n\n"
            f"Do you want to remove it from '{existing_category}' and add it to '{category}'?"
        )
        if not response:
            print(f"User cancelled adding hash to '{category}' (conflict with '{existing_category}')")
            return None
        
        # Remove hash from old rule files
        rule_remove_hash(hash_val)
    
    # Load existing rules or create new
    if rule_file.exists():
        with open(rule_file, 'r') as f:
            rules = json.load(f)
    else:
        rules = {
            "category": category,
            "examples": []
        }
    
    # Add this part as an example
    example = {"part_name": extracted_part_name, "characteristics": {"hash": hash_val}}
    
    # Remove any existing example with the same part name
    rules["examples"] = [ex for ex in rules["examples"] if ex["part_name"] != extracted_part_name]
    rules["examples"].append(example)

    # Remove duplicate examples that have identical characteristics (keep first occurrence)
    seen = set()
    unique_examples = []
    duplicates = 0
    for ex in rules.get("examples", []):
        key = json.dumps(ex.get("characteristics", {}), sort_keys=True)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique_examples.append(ex)
    rules["examples"] = unique_examples
    if duplicates > 0:
        messagebox.showinfo("Duplicate Rules", f"Removed {duplicates} duplicate examples with identical characteristics.")
    
    # Save updated rules
    with open(rule_file, 'w') as f:
        json.dump(rules, f, indent=2)

    # Update the rule mapping
    rules_category_map[hash_val] = category
    print(f"Updated rules_category_map: {hash_val}... -> {category}")

    # Update cache to include the new/updated rule
    rule_loadone(category)
    # If the hash was moved from another category, reload that one too
    if existing_category and existing_category != category:
        rule_loadone(existing_category)
    
    print(f"Saved {extracted_part_name} as example of '{category}' category")
    return rule_file

def settings_load():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                return settings
        except Exception as e:
            print(f"Error loading settings: {e}")
            return {}
    return {}

def settings_save(*args):
    settings = {
        "step_folder": folder_step_path.get(),
        "gallery_folder": folder_gallery_path.get(),
        "category": category_var.get(),
        "use_file_limit": use_file_limit.get(),
        "file_limit_size": file_limit_size.get(),
    }
    
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
        print(f"Settings saved to {SETTINGS_FILE}")
    except Exception as e:
        print(f"Error saving settings: {e}")

def show_3d_model(*args):
    """Open a new window with 3D viewer for the selected part"""
    selection = char_table.selection()
    if not selection:
        return
    
    # Get the selected item's filename
    item = selection[0]
    values = char_table.item(item, "values")
    
    if not values:
        return
    
    filename = values[1]  # First column is filename
    part_name = filename.split('.')[0] if '.' in filename else filename
    
    # Find the corresponding STEP file
    step_path = Path(folder_step_path.get())
    step_files = []
    step_files.extend(list(chain(step_path.rglob(f"{part_name}.step"), step_path.rglob(f"{part_name}.stp"))))
    
    if not step_files:
        messagebox.showinfo("File Not Found", f"No STEP file found for {part_name}")
        return
    
    step_file = step_files[0]  # Use first found file
    
    try:
        view_model = BoundingBox(step_file)
        view_model.display()
        
        print(f"Opened 3D viewer for: {step_file}")
        
    except ImportError:
        messagebox.showerror("Import Error", "occwl.viewer module not available.\nPlease install the required dependencies for 3D viewing.")
    except Exception as e:
        messagebox.showerror("Error", f"Error opening 3D viewer: {str(e)}")
        print(f"Error opening 3D viewer for {step_file}: {e}")

def show_preview(*args):
    """Update image preview when char_table selection changes"""
    global supress_preview, last_preview_path

    if supress_preview:
        return

    selection = char_table.selection()
    if not selection:
        # Clear image if no selection
        image_preview.configure(image="", text="(Image preview here)")
        last_preview_path = None
        return
    
    # Get the selected item's filename
    item = selection[0]
    values = char_table.item(item, "values")
    
    if not values:
        image_preview.configure(image="", text="(Image preview here)")
        last_preview_path = None
        return
    
    filename = char_table.set(item, "filename")  # First column is filename
    part_name = filename.split('.')[0] if '.' in filename else filename
    
    # Look for image file in gallery path
    gallery_path = folder_gallery_path.get()
    
    # Search for the image file
    image_paths = []
    for ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif']:
        image_paths.extend(Path(gallery_path).rglob(f"{part_name}{ext}"))
    
    if not image_paths:
        image_preview.configure(image="", text=f"(No image found for {part_name})")
        last_preview_path = None
        return
    
    # Use the first found image
    image_path = image_paths[0]

    # Skip loading the image if it's the same as last time
    image_path_str = str(image_path.resolve())
    if image_path_str == last_preview_path:
        return
    
    # Load and resize image to fit the preview area
    pil_image = PILImage.open(image_path)

    preview_width = image_frame.winfo_width() - 20  # 10px padding on each side
    preview_height = image_frame.winfo_height() - 40  # Account for title and padding
    
    # Calculate aspect ratio and resize
    img_width, img_height = pil_image.size
    aspect_ratio = img_width / img_height
    
    if aspect_ratio > preview_width / preview_height:
        # Image is wider - fit to width
        new_width = preview_width
        new_height = int(preview_width / aspect_ratio)
    else:
        # Image is taller - fit to height
        new_height = preview_height
        new_width = int(preview_height * aspect_ratio)
    
    # Resize image
    pil_image = pil_image.resize((new_width, new_height), PILImage.Resampling.LANCZOS)
    
    # Convert to PhotoImage
    photo_image = ImageTk.PhotoImage(pil_image)
    
    # Update the preview label
    image_preview.configure(image=photo_image, text="")
    image_preview.image = photo_image  # Keep a reference to prevent garbage collection
    last_preview_path = image_path_str
    
    print(f"Loaded image: {image_path}")

def show_tooltip(target, text_or_callback, delay=500):
    """
    Tooltip function for widgets and Treeview headers.
    
    For regular widgets: text_or_callback should be a string.
    For Treeview headers: text_or_callback should be a callable that takes a column name and returns tooltip text.
    """
    
    def hide_tooltip_window(target, tooltip_attr):
        """Generic function to hide tooltip windows"""
        job_attr = tooltip_attr.replace('tooltip', 'tooltip_job')
        
        # Cancel scheduled job if it exists
        if hasattr(target, job_attr) and getattr(target, job_attr) is not None:
            target.after_cancel(getattr(target, job_attr))
            setattr(target, job_attr, None)
        
        if hasattr(target, tooltip_attr):
            getattr(target, tooltip_attr).destroy()
            delattr(target, tooltip_attr)
    
    def show_tooltip_window(target, text, tooltip_attr):
        """Generic function to show tooltip windows"""
        x = target.winfo_pointerx()
        y = target.winfo_pointery()
        
        tooltip = tk.Toplevel()
        tooltip.wm_overrideredirect(True)
        tooltip.wm_geometry(f"+{x + 10}+{y + 10}")
        
        label = tk.Label(tooltip, text=text, background="lightyellow", 
                        relief="solid", borderwidth=1, font=("Segoe UI", 9),
                        justify=tk.LEFT, wraplength=300, padx=5, pady=2)
        label.pack()
        
        setattr(target, tooltip_attr, tooltip)
        job_attr = tooltip_attr.replace('tooltip', 'tooltip_job')
        setattr(target, job_attr, None)

    def schedule_tooltip_window(event, text, tooltip_attr):
        """Schedule showing the tooltip window after a delay"""
        job_attr = tooltip_attr.replace('tooltip', 'tooltip_job')
        if hasattr(target, job_attr) and getattr(target, job_attr) is not None:
            target.after_cancel(getattr(target, job_attr))
        
        job_id = target.after(delay, lambda: show_tooltip_window(target, text, tooltip_attr))
        setattr(target, job_attr, job_id)
    
    if isinstance(target, ttk.Treeview):
        # Handle Treeview headers
        current_col = None
        
        def on_motion(event):
            nonlocal current_col
            
            region = target.identify_region(event.x, event.y)
            
            if region == "heading":
                col = target.identify_column(event.x)
                if col:
                    col_index = int(col[1:]) - 1
                    if 0 <= col_index < len(target["columns"]):
                        col_name = target["columns"][col_index]
                        
                        if col_name != current_col:
                            current_col = col_name
                            hide_tooltip_window(target, 'header_tooltip')
                            
                            if callable(text_or_callback):
                                tooltip_text = text_or_callback(col_name)
                                if tooltip_text:
                                    schedule_tooltip_window(event, tooltip_text, 'header_tooltip')
            else:
                if current_col is not None:
                    current_col = None
                    hide_tooltip_window(target, 'header_tooltip')
        
        def on_leave(event):
            nonlocal current_col
            current_col = None
            hide_tooltip_window(target, 'header_tooltip')
        
        target.bind('<Motion>', on_motion)
        target.bind('<Leave>', on_leave)
    
    else:
        # Handle regular widgets
        def on_enter(event):
            schedule_tooltip_window(event, text_or_callback, 'tooltip')
        
        def on_leave(event):
            hide_tooltip_window(target, 'tooltip')
        
        target.bind("<Enter>", on_enter)
        target.bind("<Leave>", on_leave)

def summary_stats_update():
    """Update min/max summary"""
    # Clear and rebuild
    summary_table.delete(*summary_table.get_children())
    
    items = char_table.get_children()
    if not items:
        return
    
    # Get all values for numeric columns
    data = {}
    for item in items:
        values = char_table.item(item, "values")
        for i, col in enumerate(char_table["columns"]):
            if col == "index":
                continue
            if i < len(values) and char_columns[col][3] in (int, float):
                if values[i] is None or (isinstance(values[i], str) and values[i].strip() == ""):
                    continue
                data.setdefault(col, []).append(float(values[i]))
    
    # Build rows with list comprehension
    def format_value(col, val):
        return f"{val:.2f}" if char_columns[col][3] == float else str(int(val))
    
    min_row = [
        "MIN:" if col == "filename" else "" if col not in data else format_value(col, min(data[col])) for col in char_table["columns"]
    ]

    common_row = [
        "MEAN:" if col == "filename" else "" if col not in data else get_most_common_values(data, col) for col in char_table["columns"]
    ]
    
    max_row = [
        "MAX:" if col == "filename" else "" if col not in data else format_value(col, max(data[col])) for col in char_table["columns"]
    ]
    
    summary_table.insert("", tk.END, values=min_row)
    summary_table.insert("", tk.END, values=common_row)
    summary_table.insert("", tk.END, values=max_row)

def toggle_file_limit(*args):
    """Enable/disable file limit size entry based on toggle state"""
    if use_file_limit.get():
        file_limit_entry.configure(state="normal")
        file_limit_size_label.configure(state="normal")
    else:
        file_limit_entry.configure(state="disabled")
        file_limit_size_label.configure(state="disabled")
    settings_save()

if __name__ == '__main__':
    multiprocessing.freeze_support()  # Required for Windows

    root = tk.Tk()
    root.title("Categorize Dataset")

    style = ttk.Style()
    # Available themes: 'clam', 'alt', 'default', 'classic', 'vista', 'xpnative', 'winnative'
    style.theme_use("vista")
    theme_bg = style.lookup("TFrame", "background")

    style.configure("Title.TLabelframe.Label", font=('TkDefaultFont', 10, 'bold'))
    style.configure("Title.TLabelframe", borderwidth=2, relief="groove", background=theme_bg)
    style.configure("Treeview.Heading", background="#CCCCCC")
    style.configure("Treeview", selectmode="browse", font=('TkFixedFont', 8))

    mainframe = ttk.Frame(root, padding="8 8 8 8") # Left, Top, Right, Bottom
    mainframe.grid(column=0, row=0, sticky=(N, W, E, S))
    innerframe = ttk.Frame(mainframe, padding="5 5 5 5", borderwidth=2, relief="groove")
    innerframe.grid(column=0, row=0, sticky=(N, W, E, S))

    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    mainframe.columnconfigure(0, weight=1)
    mainframe.rowconfigure(0, weight=1)

    innerframe.columnconfigure(1, weight=1)
    innerframe.rowconfigure(2, weight=1)

    setup_frame = ttk.LabelFrame(innerframe, text="Setup")
    setup_frame.grid(column=0, row=0, sticky=(N, W, E, S))
    setup_frame.columnconfigure(1, weight=1)

    setup_toggle_frame = ttk.Frame(setup_frame)
    setup_toggle_frame.grid(column=4, row=0, rowspan=4 ,sticky=(W, E))
    setup_toggle_frame.columnconfigure(1, weight=1)

    # Create action bar
    action_frame = ttk.LabelFrame(innerframe, text="Actions")
    action_frame.grid(column=0, row=1, sticky=(N, W, E, S))
    action_frame.columnconfigure(6, weight=1)

    # Image preview
    image_frame = ttk.LabelFrame(innerframe, text="Preview", width=240, height=160)
    image_frame.grid(column=1, row=0, rowspan=2, sticky=(N, W, E, S))
    image_frame.grid_propagate(False)  # Prevent frame from resizing to fit contents
    image_frame.columnconfigure(0, weight=1)
    image_frame.rowconfigure(0, weight=1)

    rule_frame = ttk.LabelFrame(innerframe, text="Rules")
    rule_frame.grid(column=1, row=2, sticky=(N, W, E, S))

    rule_table_frame = ttk.Frame(rule_frame)
    rule_table_frame.grid(column=0, row=0, columnspan=2, sticky=(N, W, E, S))

    char_frame = ttk.LabelFrame(innerframe, text="Characteristics")
    char_frame.grid(column=0, row=2, sticky=(N, W, E, S))
    char_frame.columnconfigure(0, weight=1)

    # Progress bar frame
    #progress_frame = ttk.LabelFrame(innerframe, text="Status")
    progress_frame = ttk.Frame(innerframe, padding="0 2 0 0")
    progress_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E))
    progress_frame.columnconfigure(1, weight=1)

    # Progress bar and label
    progress_label = ttk.Label(progress_frame, text="Ready", width=50, anchor="w", font=("Courier", 9))
    progress_label.grid(row=0, column=0, sticky=tk.W)

    progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
    progress_bar.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 0))

    # Input variables
    rule_name_new = tk.StringVar()
    part_name = tk.StringVar()
    folder_step_path = tk.StringVar()
    folder_gallery_path = tk.StringVar()
    category_var = tk.StringVar()
    category_display_var = tk.StringVar()
    use_file_limit = tk.BooleanVar(value=True)
    file_limit_size = tk.IntVar(value=12000)
    hide_categorized = tk.BooleanVar(value=False)
    color_instances = tk.BooleanVar(value=True)
    use_cache = tk.BooleanVar(value=True)
    disp_axis_var = tk.BooleanVar(value=True)
    no_background_var = tk.BooleanVar(value=False)

    # Buttons
    generate_gallery_button = ttk.Button(action_frame, text="Generate Gallery", command=gallery_generate_start, state="disabled", width=15)
    generate_gallery_button.grid(column=0, row=0, sticky=W)

    analyze_button = ttk.Button(action_frame, text="Analyze", command=analyze_start, state="disabled")
    analyze_button.grid(column=1, row=0, sticky=W)

    reclassify_button = ttk.Button(action_frame, text="Reclassify", command=lambda: analyze_start("reclassify"), state="disabled")
    reclassify_button.grid(column=2, row=0, sticky=W)

    categorize_button = ttk.Button(action_frame, text="Categorize", command=lambda: analyze_start("categorize"), state="disabled")
    categorize_button.grid(column=3, row=0, sticky=(W, E))

    generate_csv_button = ttk.Button(action_frame, text="Export CSV", command=csv_export, state="normal")
    generate_csv_button.grid(column=4, row=0, sticky=W)

    lookup_button = ttk.Button(action_frame, text="Lookup", command=lookup, state="disabled")
    lookup_button.grid(column=7, row=0, sticky=W)

    folder_step_button = ttk.Button(setup_frame, text="Browse", command=lambda: browse_folder(folder_step_path))
    folder_step_button.grid(column=2, row=0, sticky=W)

    folder_gallery_button = ttk.Button(setup_frame, text="Browse", command=lambda: browse_folder(folder_gallery_path))
    folder_gallery_button.grid(column=2, row=1, sticky=W)

    add_rule_button = ttk.Button(rule_frame, text="Add Rule", command=rule_create, state="disabled")
    add_rule_button.grid(column=1, row=2, sticky=W)

    category_refresh_button = ttk.Button(setup_frame, text="Refresh", command=populate_category_dropdown)
    category_refresh_button.grid(column=2, row=2, sticky=W)

    # Load settings and populate folder paths
    settings = settings_load()
    if settings.get("step_folder"):
        folder_step_path.set(settings["step_folder"])
    if settings.get("gallery_folder"):
        folder_gallery_path.set(settings["gallery_folder"])
    if settings.get("category"):
        category_var.set(settings["category"])
    if settings.get("use_file_limit") is not None:
        use_file_limit.set(settings["use_file_limit"])
    if settings.get("file_limit_size") is not None:
        file_limit_size.set(settings["file_limit_size"])

    analyze_button_update()

    rule_name_entry = ttk.Entry(rule_frame, width=20, textvariable=rule_name_new)
    rule_name_entry.grid(column=0, row=2, sticky=(W, E))

    part_name_entry = ttk.Entry(action_frame, width=20, textvariable=part_name)
    part_name_entry.grid(column=6, row=0, sticky=(W, E))

    folder_path_entry = ttk.Entry(setup_frame, width=20, textvariable=folder_step_path)
    folder_path_entry.grid(column=1, row=0, sticky=(W, E))

    gallery_path_entry = ttk.Entry(setup_frame, width=20, textvariable=folder_gallery_path)
    gallery_path_entry.grid(column=1, row=1, sticky=(W, E))

    category_dropdown = ttk.Combobox(setup_frame, textvariable=category_display_var, state="readonly", width=18)
    category_dropdown.grid(column=1, row=2, sticky=(W, E))
    category_dropdown.configure(height=24)
    ttk.Label(setup_frame, text="Category:").grid(column=0, row=2, sticky=W)

    # File limit toggle
    file_limit_check = ttk.Checkbutton(setup_toggle_frame, text="Use File Limit", variable=use_file_limit, command=toggle_file_limit)
    file_limit_check.grid(column=0, row=0, sticky=W)

    # File limit size entry
    file_limit_entry = ttk.Entry(setup_toggle_frame, width=10, textvariable=file_limit_size)
    file_limit_entry.grid(column=0, row=1, sticky=W)

    # Hide categorized toggle
    hide_categorized_check = ttk.Checkbutton(setup_toggle_frame, text="Hide categorized", variable=hide_categorized, command=char_table_hide_categorized)
    hide_categorized_check.grid(column=0, row=2, sticky=W)

    # Color instances toggle
    color_instances_check = ttk.Checkbutton(setup_toggle_frame, text="Color instances", variable=color_instances, command=char_table_row_color)
    color_instances_check.grid(column=0, row=3, sticky=W)

    # Use cache toggle
    use_cache_check = ttk.Checkbutton(setup_toggle_frame, text="Use cache", variable=use_cache)
    use_cache_check.grid(column=0, row=4, sticky=W)
    
    # Show axis toggle
    disp_axis_check = ttk.Checkbutton(setup_toggle_frame, text="Display axis", variable=disp_axis_var)
    disp_axis_check.grid(column=0, row=7, sticky=W)
    
    # Render background toggle
    no_background_check = ttk.Checkbutton(setup_toggle_frame, text="No background (disables AA)", variable=no_background_var)
    no_background_check.grid(column=0, row=8, sticky=W)

    # Labels
    ttk.Label(action_frame, text="Part name:").grid(column=5, row=0, sticky=W)
    instance_label = ttk.Label(action_frame, text="Instances:", width=20, anchor="w", font=("Courier", 9))
    instance_label.grid(column=8, row=0, sticky=W)
    instance_label_count = tk.StringVar(value="0")
    instance_count_label = ttk.Label(action_frame, textvariable=instance_label_count, width=4, anchor="w", font=("Courier", 9))
    instance_count_label.grid(column=9, row=0, sticky=W)

    ttk.Label(setup_frame, text="Step:").grid(column=0, row=0, sticky=W)
    ttk.Label(setup_frame, text="Gallery:").grid(column=0, row=1, sticky=W)
    file_limit_size_label = ttk.Label(setup_toggle_frame, text="Size in KB")
    file_limit_size_label.grid(column=1, row=1, sticky=W)

    for child in innerframe.winfo_children(): 
        child.grid_configure(padx=5)

    for child in rule_frame.winfo_children(): 
        child.grid_configure(padx=5, pady=5)

    for child in setup_frame.winfo_children(): 
        child.grid_configure(padx=5, pady=5)

    for child in action_frame.winfo_children():
        child.grid_configure(padx=5, pady=5)

    for child in progress_frame.winfo_children():
        child.grid_configure(padx=5, pady=5)

    for child in setup_toggle_frame.winfo_children():
        child.grid_configure(padx=5, pady=2)

    # Create Treeview
    char_table = ttk.Treeview(char_frame, height=20)
    char_table.grid(padx=5, sticky=(N, W, E, S))

    # Create scrollbars
    char_table_vscroll = ttk.Scrollbar(char_frame, orient=tk.VERTICAL, command=char_table.yview)

    # Configure treeview scrolling
    char_table.configure(yscrollcommand=char_table_vscroll.set)

    # Grid layout
    char_table.grid(row=0, column=0, sticky="nsew")
    char_table_vscroll.grid(row=0, column=1, sticky="ns")

    # Configure grid weights
    char_frame.grid_rowconfigure(0, weight=1)
    char_frame.grid_columnconfigure(0, weight=1)

    char_small = 25
    char_normal = 35
    char_bool = 35
    char_vol = 85
    char_bbox = 50

    # Configure headings and columns
    char_columns = {
        "index": ("#", 30, "e", int, "Index"),
        "filename": ("File Name", 125, "w", str, ""),
        "category": ("Category", 85, "w", str, ""),
        "color_hash": ("Instance", 60, "w", str, "Color Hash for instances"),
        "solids": ("S", 20, "e", int, "Solids"),
        "shells": ("Sh", 20, "e", int, "Shells"),
        "edges": ("E", char_normal, "e", int, "Edges"),
        "edge_line": ("El", char_small, "e", int, "Edge - Line"),
        "edge_curved": ("Ec", char_small, "e", int, "Edge - Curved"),
        "edge_long": ("Elg", char_small, "e", int, "Edges matching the longest BBox dimension"),
        "edge_mid": ("Em", char_small, "e", int, "Edges matching the middle BBox dimension"),
        "edge_short": ("Esh", char_small, "e", int, "Edges matching the shortest BBox dimension"),
        "edge_irregular": ("Eir", char_small, "e", int, "Edges that do not match any BBox dimension"),
        "edge_circular": ("Ecr", char_small, "e", int, "Edge - Circular"),
        "edge_bspline": ("Ebs", char_small, "e", int, "Edge - B-Spline"),
        "edge_x_aligned": ("Ex", char_small, "e", int, "Edge - X aligned"),
        "edge_y_aligned": ("Ey", char_small, "e", int, "Edge - Y aligned"),
        "edge_z_aligned": ("Ez", char_small, "e", int, "Edge - Z aligned"),
        "edge_axis_aligned": ("Eax", char_small, "e", int, "Edge - Aligned to any primary axis"),
        "edge_none_aligned": ("Ena", char_small, "e", int, "Edge - Not aligned to any primary axis"),
        "faces": ("F", char_normal, "e", int, "Faces"),
        "face_planar": ("Fpl", char_normal, "e", int, "Face - Planar"),
        "face_curved": ("Fcv", char_normal, "e", int, "Face - Curved"),
        "vertices": ("V", char_normal, "e", int, "Vertices"),
        "wires": ("W", char_normal, "e", int, "Wires"),
        "holes": ("H", char_normal, "e", int, "Number of holes"),
        "area": ("Area", char_vol, "e", float, "Surface Area"),
        "volume": ("Volume", char_vol, "e", float, "Volume"),
        "bbox_vol": ("BVol", char_vol, "e", float, "Bounding Box Volume"),
        "vol_dif": ("Vdif%", char_bbox, "e", float, "Volume Difference % (Calculated Volume vs BBox Volume)"),
        "bbox_x": ("BBox x", char_bbox, "e", float, "Bounding Box X dimension"),
        "bbox_y": ("BBox y", char_bbox, "e", float, "Bounding Box Y dimension"),
        "bbox_z": ("BBox z", char_bbox, "e", float, "Bounding Box Z dimension"),
        "bbox_square": ("Bsq", char_bool, "e", bool, "Is Bounding Box square same in X, Y, Z"),
        "round_faces": ("Rnd", char_bool, "e", bool, "Is 60 percent or more faces rounded"),
        "perpendicular_faces": ("Perp", char_bool, "e", bool, "Are all the faces perpendicular"),
        "volumes_match": ("Vmatch", char_bool, "e", bool, "Does calculated volume match bbox volume"),
        "holed": ("Holed", char_bool, "e", bool, "Does the part have holes")
    }

    # Define columns
    char_table["show"] = "headings"
    char_table["columns"] = list(char_columns.keys())

    for col, (heading, width, anchor, dtype, tooltip) in char_columns.items():
        char_table.heading(col, text=heading, command=lambda c=col: char_table_sort_column(c, False))
        char_table.column(col, width=width, anchor=anchor)

    # Create summary table
    summary_frame = ttk.Frame(char_frame)
    summary_frame.grid(row=2, column=0, columnspan=1, sticky=(tk.W, tk.E), padx=5, pady=5)

    summary_table = ttk.Treeview(summary_frame, height=3)
    summary_table.grid(row=0, column=0, sticky="ew")
    summary_frame.columnconfigure(0, weight=1)

    # Configure to match main table
    summary_table["columns"] = list(char_columns.keys())
    summary_table["show"] = "headings"

    # Set the headings for the summary table
    for col, (heading, width, anchor, dtype, tooltip) in char_columns.items():
        summary_table.heading(col, text=heading if heading not in ["#", "File Name", "Category"] else "")
        summary_table.column(col, width=width, anchor=anchor)

    # Remove the duplicate function definitions and just call once:
    summary_stats_update()

    image_preview = ttk.Label(image_frame, text="(Image preview here)", anchor="center")
    image_preview.grid(column=0, row=0, sticky=(N, W, E, S), padx=5, pady=5)

    # Create Treeview
    rule_table = ttk.Treeview(rule_table_frame)

    # Create scrollbars
    rule_table_vscroll = ttk.Scrollbar(rule_table_frame, orient=tk.VERTICAL, command=rule_table.yview)

    # Configure treeview scrolling
    rule_table.configure(yscrollcommand=rule_table_vscroll.set)

    # Grid layout
    rule_table.grid(row=0, column=0, sticky="nsew")
    rule_table_vscroll.grid(row=0, column=1, sticky="ns")

    # Configure grid weights
    rule_frame.grid_rowconfigure(0, weight=1)
    rule_frame.grid_columnconfigure(0, weight=1)
    rule_table_frame.grid_rowconfigure(0, weight=1)
    rule_table_frame.grid_columnconfigure(0, weight=1)

    # Define columns
    rule_table["columns"] = ("name", "examples", "matches")
    rule_table["show"] = "headings"

    # Configure headings and columns
    rule_columns = {
        "name": ("Rule Name", 140, "w"),
        "examples": ("Examples", 40, "e"),
        "matches": ("Matches", 40, "e"),
    }

    for col, (heading, width, anchor) in rule_columns.items():
        rule_table.heading(col, text=heading)
        rule_table.column(col, width=width, anchor=anchor)

    # Update add rule button state based on selection and input
    char_table.bind("<<TreeviewSelect>>", get_instances)
    char_table.bind("<<TreeviewSelect>>", rule_button_update, add="+")
    char_table.bind("<<TreeviewSelect>>", show_preview, add="+")
    char_table.bind("<FocusIn>", rule_button_update)
    char_table.bind("<FocusOut>", rule_button_update)
    rule_name_new.trace_add("write", rule_button_update)
    rule_table.bind("<Double-1>", rule_on_double_click)

    # Update lookup button state based on part name input
    part_name.trace_add("write", lookup_button_update)
    folder_step_path.trace_add("write", gallery_button_update)
    folder_gallery_path.trace_add("write", gallery_button_update)

    # Update settings when folder paths change
    folder_step_path.trace_add("write", settings_save)
    folder_step_path.trace_add("write", analyze_button_update)
    folder_gallery_path.trace_add("write", settings_save)
    folder_gallery_path.trace_add("write", analyze_button_update)
    folder_gallery_path.trace_add("write", populate_category_dropdown)
    category_dropdown.bind("<<ComboboxSelected>>", on_category_selected)
    file_limit_entry.bind("<Return>", settings_save)
    file_limit_entry.bind("<FocusOut>", settings_save)

    image_preview.bind("<Double-Button-1>", show_3d_model)

    # Keyboard bindings
    part_name_entry.focus()
    char_table.bind("<Control-c>", copy_selected_filename)
    char_table.bind("<Control-C>", copy_selected_filename)
    part_name_entry.bind("<Return>", on_part_name_enter)
    rule_name_entry.bind("<Return>", on_rule_name_enter)
    rule_table.bind("<Delete>", rule_delete)
    rule_table.bind("<Button-3>", rule_on_right_click)
    root.bind_all("<Control-Shift-v>", lookup_paste)
    root.bind_all("<Control-Shift-V>", lookup_paste)

    show_tooltip(image_preview, "Double-click to open 3D viewer")
    show_tooltip(char_table, get_header_tooltip)
    populate_category_dropdown()
    rule_populate()
    toggle_file_limit()
    gallery_button_update()
    root.mainloop()
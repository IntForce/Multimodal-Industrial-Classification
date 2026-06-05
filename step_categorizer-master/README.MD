# STEP Categorizer
A tool to help speed up a dataset categorization by letting you see characteristics of each step file and create rules to classify step files with similar characteristics.

Has a few helpful options, such as color coding similar step files and listing how many files with similar characteristics there are.

# Usage
Set the ```step``` and ```gallery``` folder and generate the gallery of step files.

Select which category (default is the root of the gallery folder) and click ```Analyze``` to list all the step files and their characteristics. Set up rules by selecting a step file from the table, giving a name to the rule and clicking ```Add Rule```.

Selecting a step file from the table will show a preview of that file. Double click the preview to open a 3D viewer of the step file, for more detailed inspection.

# Dependencies
- OCCWL
- PIL

# TODO
- ~~Add menu to 3D viewer with shortcuts to view the model from 6 different sides~~
- Make 3D Viewer open in a subroutine so it doesn't block whatever process is running
- Make it possible to show bounding box in 3D view
import numpy as np
import cv2
import os
import concurrent.futures # https://docs.python.org/3/library/concurrent.futures.html

class EdgeExtractor:
    """
    This class module is for extracting edge contours given a series of input images.
    The class takes a directory containing images and applies a Sobel filter to detect and extract the contours present in each image.
    Edge contours are saved to a specified directory.
    """
    def __init__(self):
        """
        Initializes the EdgeExtractor module.
        """
        self._imageDirectory: str = ""
        self._edgeDirectory: str = ""
            
    # interface
    def setImageDirectory(self, path: str):
        """
        Setter for the source directory that contains the images to be processed.

        Args:
            path (str): Absolute path for the source image directory.
        """
        if path == "":
            print("[EdgeExtractor] > Path must be specified for the image directory!")
            return
        
        self._imageDirectory = path
        print(f"[EdgeExtractor] > Image directory has been set to: {path}.")
    
    def setEdgeDirectory(self, path: str):
        """
        Setter for the destination directory that edge contours will be saved to.

        Args:
            path (str): Absolute source directory path.
        """
        if path == "":
            print("[EdgeExtractor] > Path must be specified for the edge directory!")
            return
        
        self._edgeDirectory = path
        print(f"[EdgeExtractor] > Edge directory has been set to: {path}.\nThe contours to be generated will be saved to this path.")
    
    def generateEdgeLibrary(self):
        """
        Generates the edge contours. The function scans the image directory for images and utilizes `concurrent.futures` to process images asynchously.
        The images are processed with a Canny derivative filter and saved to the source directory path.
        
        Returns:
            None.
        """
        if self._imageDirectory == "" or self._edgeDirectory == "":
            print("[EdgeExtractor] > Path for the image directory or edge directory is empty. These must be specified!")
            return
        
        print(f"[EdgeExtractor] > Initialized the generation of edge contours.")
        
        # get images
        files = os.listdir(self._imageDirectory)
        images = []
        
        for f in files:
            if f.lower().endswith((".png", ".jpg", ".jpeg")):
                images.append(os.path.join(self._imageDirectory, f)) # append full image path to filename and add it to the images list
        

        # process and save images
        total_images = len(images)
        # https://medium.com/@smrati.katiyar/introduction-to-concurrent-futures-in-python-009fe1d4592c
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(self._processor, img) for img in images]
        
            i = 1
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                print(f"[EdgeExtractor] > ({i}/{total_images}) {res}")
                i += 1
            
            print(f"[EdgeExtractor] > The generation of the edge contour library has completed successfully.")
        
    
    # ===== canny algo ===
    # inspiration: https://machinelearningprojects.net/sobel-x-and-sobel-y-in-cv2/
    def _canny(self, image):
        """
        This function applies the canny filter where it identifies intensity gradients to retrieve the edge contours.

        Args:
            image (np.ndarray): Input image to be processed.

        Returns:
            np.ndarray: The resulting edge contour.
        """
        # sobelXimage = cv2.Sobel(image, cv2.CV_64F, 1,0, ksize=5)
        # sobelYimage = cv2.Sobel(image, cv2.CV_64F, 0,1, ksize=5)
        cannyImage = cv2.Canny(image, 10, 50)
        return cannyImage
    
    def _processor(self, imagePath : str):
        """
        This helper function is used to execute the program asynchrously. It takes images and converts the colorspace from BGR to RGB
        and applies the class sobel function to them.

        Args:
            imagePath (str): Absolute path for the specific image that wil be processed.
        Returns:
            None.
        """
        imageToProcess = cv2.imread(imagePath)
        if imageToProcess is None:
            print(f"[EdgeExtractor] > Failed to read image: {imagePath}. Skipping.")
            return
        
        imageToProcess = cv2.cvtColor(imageToProcess, cv2.COLOR_BGR2GRAY)
        edges = self._canny(image=imageToProcess)
        
        name = os.path.basename(imagePath)
        output = os.path.join(self._edgeDirectory, name)
        
        cv2.imwrite(output, edges)
        # print(f"[EdgeExtractor] > Contours has been generated and saved to {self._edgeDirectory}")
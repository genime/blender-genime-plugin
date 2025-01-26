import bpy
import base64
import gzip
import requests
import tempfile
import os
import threading
import uuid

bl_info = {
    "name": "Make3D",
    "author": "Ishan Gupta",
    "version": (2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Make3D",
    "description": "Generate 3D models given image prompts",
    "category": "Animation",
}

SERVER_URL = "https://genime--comfy-3d-app-comfyui-api.modal.run"

class MAKE3D_OT_generate_model(bpy.types.Operator):
    bl_idname = "make3d.generate_model"
    bl_label = "Generate 3D Model"
    bl_options = {'REGISTER', 'UNDO'}


    _timer = None
    _thread = None
    _is_running = False
    _temp_file_path = None
    _model_format = None

    @classmethod
    def poll(cls, context):
        return not context.scene.make3d_is_running
    

    def execute(self, context):
        if not context.scene.make3d_image_path:
            self.report({'ERROR'}, "No image selected. Please select an image first.")
            return {'CANCELLED'}

        if not context.scene.make3d_user_key:
            self.report({'ERROR'}, "No API key provided. Please add your key in the Settings.")
            return {'CANCELLED'}

        context.scene.make3d_is_running = True
        self._is_running = True
        self._thread = threading.Thread(target=self.generate_model, args=(context,))
        self._thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}
    
    def modal(self, context, event):
        if event.type == 'TIMER':
            if not self._is_running:
                self.cancel(context)
                return {'FINISHED'}
        return {'PASS_THROUGH'}
    
    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        self._is_running = False
        context.scene.make3d_is_running = False
        self.report({'INFO'}, "3D model generation cancelled")

    def generate_model(self, context):
        scene = context.scene
        image_path = scene.make3d_image_path
        timeout_minutes = scene.make3d_timeout_minutes
        output_folder_path = scene.make3d_output_dir

        # Read the selected image
        try:
            with open(image_path, 'rb') as img_file:
                image_data = img_file.read()
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read image: {str(e)}")
            self._is_running = False
            context.scene.make3d_is_running = False
            return

        # Send image to Comfy server
        try:
            response = self.send_to_comfy_server(image_data, timeout_minutes)
        except Exception as e:
            self.report({'ERROR'}, f"Error contacting server: {str(e)}")
            self._is_running = False
            context.scene.make3d_is_running = False
            return

        if response.status_code == 200:
            result = response.json()
            if 'files' not in result:
                self.report({'ERROR'}, "No files received in response")
                self._is_running = False
                context.scene.make3d_is_running = False
                return

            # Debug print to see what files we're receiving
            self.report({'INFO'}, f"Received files: {list(result['files'].keys())}")

            # Check for either GLB or OBJ file by extension pattern
            model_file_key = None
            model_format = None
            
            # Find first file ending with .glb.gz or .obj.gz
            for file_key in result['files'].keys():
                if file_key.endswith('.glb.gz'):
                    model_file_key = file_key
                    model_format = 'glb'
                    break
                elif file_key.endswith('.obj.gz'):
                    model_file_key = file_key
                    model_format = 'obj'
                    break

            if not model_file_key:
                self.report({'ERROR'}, f"No supported model format found in response. Available files: {list(result['files'].keys())}")
                self._is_running = False
                context.scene.make3d_is_running = False
                return

            compressed_file_content = result['files'][model_file_key]
            compressed_file_path = os.path.join(output_folder_path, model_file_key)
            decompressed_file_path = os.path.join(output_folder_path, model_file_key.replace('.gz', ''))

            # Save the compressed file locally
            try:
                with open(compressed_file_path, 'wb') as compressed_file:
                    compressed_file.write(base64.b64decode(compressed_file_content))
            except Exception as e:
                self.report({'ERROR'}, f"Failed to save compressed file: {str(e)}")
                self._is_running = False
                context.scene.make3d_is_running = False
                return

            # Decompress the file
            try:
                with gzip.open(compressed_file_path, 'rb') as compressed_file:
                    with open(decompressed_file_path, 'wb') as decompressed_file:
                        decompressed_file.write(compressed_file.read())
                self.report({'INFO'}, f"Decompressed file saved at: {decompressed_file_path}")
            except Exception as e:
                self.report({'ERROR'}, f"Failed to decompress file: {str(e)}")
                self._is_running = False
                context.scene.make3d_is_running = False
                return

            self._temp_file_path = decompressed_file_path
            self._model_format = model_format
            bpy.app.timers.register(self.import_model_on_main_thread)
            self.report({'INFO'}, "3D model successfully processed.")
        else:
            self.report({'ERROR'}, f"Failed to receive a valid response. Status code: {response.status_code}")

        self._is_running = False
        context.scene.make3d_is_running = False

    def send_to_comfy_server(self, image_data, timeout_minutes):
        url = SERVER_URL
    
        files = {
            'image': ('image.png', image_data, 'image/png')
        }

        params = {
            'workflow_name': 'trellis',
            'compress_mesh': True,
            'user_key': bpy.context.scene.make3d_user_key
        }

        return requests.post(url, files=files, params=params, timeout=timeout_minutes * 60 + 60)


    def download_model(self, model_url):
        response = requests.get(model_url, stream=True, timeout=300)
        if response.status_code == 200:
            temp_dir = tempfile.gettempdir()
            model_filename = os.path.basename(model_url)
            model_path = os.path.join(temp_dir, model_filename)
            with open(model_path, 'wb') as model_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        model_file.write(chunk)
            return model_path
        else:
            raise Exception(f"Failed to download model. Status code: {response.status_code}")
        
    
    
    def import_model_on_main_thread(self):
        """Imports the model on Blender's main thread."""
        if self._temp_file_path:
            try:
                # Import based on format
                if self._model_format == 'glb':
                    bpy.ops.import_scene.gltf(filepath=self._temp_file_path)
                else:  # obj format
                    bpy.ops.wm.obj_import(filepath=self._temp_file_path)

                self.report({'INFO'}, f"3D model successfully imported as {self._model_format.upper()}")
            except Exception as e:
                self.report({'ERROR'}, f"Failed to import model: {str(e)}")

            # Cleanup the temp file
            # os.remove(self._temp_file_path)
            self._temp_file_path = None
            self._model_format = None

        # Update the is_running flag now that we are done
        bpy.context.scene.make3d_is_running = False
        return None


class MAKE3D_OT_select_image(bpy.types.Operator):
    bl_idname = "make3d.select_image"
    bl_label = "Select Image"
    bl_description = "Select an image file for 3D model generation"

    filepath = bpy.props.StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No file selected.")
            return {'CANCELLED'}
        context.scene.make3d_image_path = self.filepath
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MAKE3D_OT_select_output_dir(bpy.types.Operator):
    bl_idname = "make3d.select_output_dir"
    bl_label = "Select Output Directory"
    bl_description = "Select the directory where the decompressed 3D model will be saved"
    bl_options = {'REGISTER', 'UNDO'}

    directory = bpy.props.StringProperty(
        name="Directory",
        description="Directory to save the decompressed 3D model",
        subtype='DIR_PATH'
    )

    def execute(self, context):
        context.scene.make3d_output_dir = self.directory
        self.report({'INFO'}, f"Output directory set to: {self.directory}")
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MAKE3D_OT_settings(bpy.types.Operator):
    bl_idname = "make3d.settings"
    bl_label = "3D Generation Settings"
    bl_options = {'REGISTER', 'INTERNAL'}

    timeout_minutes: bpy.props.IntProperty(
        name="Timeout (Minutes)",
        description="Maximum time to wait for server response",
        default=10,
        min=1,
        max=30
    )

    user_key: bpy.props.StringProperty(
        name="API Key",
        description="Your API key for the service",
        default="",
        subtype='PASSWORD'  # This will show the key as dots for security
    )

    def execute(self, context):
        scene = context.scene
        scene.make3d_timeout_minutes = self.timeout_minutes
        scene.make3d_user_key = self.user_key
        return {'FINISHED'}

    def invoke(self, context, event):
        scene = context.scene
        self.timeout_minutes = scene.make3d_timeout_minutes
        self.user_key = scene.make3d_user_key
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "user_key")
        layout.prop(self, "timeout_minutes")


class MAKE3D_PT_panel(bpy.types.Panel):
    bl_label = "3D Model Generator"
    bl_idname = "MAKE3D_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Make3D'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.operator("make3d.settings", text="Settings", icon='PREFERENCES')
        
        row = layout.row()
        row.operator("make3d.select_image", text="Select Image", icon='FILE_FOLDER')
        row.prop(scene, "make3d_image_path", text="")

        # Select Output Directory
        row = layout.row()
        row.operator("make3d.select_output_dir", text="Select Output Directory", icon='FILE_FOLDER')
        row.prop(scene, "make3d_output_dir", text="")

        layout.prop(scene, "make3d_timeout_minutes")

        if scene.make3d_is_running:
            layout.label(text="Generating 3D model...", icon='RENDER_STILL')
        else:
            layout.operator("make3d.generate_model", text="Generate 3D Model")


class MAKE3D_OT_cancel(bpy.types.Operator):
    bl_idname = "make3d.cancel"
    bl_label = "Cancel Generation"
    bl_description = "Cancel the ongoing 3D model generation process"

    def execute(self, context):
        if context.scene.make3d_is_running:
            context.scene.make3d_is_running = False
            self.report({'INFO'}, "Cancelling 3D model generation process...")
        return {'FINISHED'}
    

def register():
    bpy.utils.register_class(MAKE3D_OT_generate_model)
    bpy.utils.register_class(MAKE3D_OT_select_image)
    bpy.utils.register_class(MAKE3D_OT_select_output_dir)
    bpy.utils.register_class(MAKE3D_PT_panel)
    bpy.utils.register_class(MAKE3D_OT_settings)
    bpy.utils.register_class(MAKE3D_OT_cancel)

    bpy.types.Scene.make3d_image_path = bpy.props.StringProperty(
        name="Image Path",
        description="Path to the input image",
        subtype='FILE_PATH'
    )

    bpy.types.Scene.make3d_timeout_minutes = bpy.props.IntProperty(
        name="Timeout (Minutes)",
        description="Maximum time to wait for server response",
        default=20,
        min=1,
        max=30
    )

    bpy.types.Scene.make3d_output_dir = bpy.props.StringProperty(
        name="Output Directory",
        description="Directory to save the decompressed 3D model",
        subtype='DIR_PATH',
        default=""  # You can set a default path if desired
    )

    bpy.types.Scene.make3d_is_running = bpy.props.BoolProperty(default=False)

    bpy.types.Scene.make3d_user_key = bpy.props.StringProperty(
        name="API Key",
        description="Your API key for the service",
        default="",
        subtype='PASSWORD'
    )


def unregister():
    bpy.utils.unregister_class(MAKE3D_OT_generate_model)
    bpy.utils.unregister_class(MAKE3D_OT_select_image)
    bpy.utils.unregister_class(MAKE3D_PT_panel)
    bpy.utils.unregister_class(MAKE3D_OT_settings)
    bpy.utils.unregister_class(MAKE3D_OT_cancel)
    bpy.utils.unregister_class(MAKE3D_OT_select_output_dir)

    del bpy.types.Scene.make3d_image_path
    del bpy.types.Scene.make3d_timeout_minutes
    del bpy.types.Scene.make3d_is_running
    del bpy.types.Scene.make3d_user_key

if __name__ == "__main__":
    register()
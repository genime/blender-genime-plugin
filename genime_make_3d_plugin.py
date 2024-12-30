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


    @classmethod
    def poll(cls, context):
        return not context.scene.make3d_is_running
    

    def execute(self, context):
        if not context.scene.make3d_image_path:
            self.report({'ERROR'}, "No image selected. Please select an image first.")
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
            if 'files' in result and 'mesh_t_1.obj.gz' in result['files']:
                compressed_file_content = result['files']['mesh_t_1.obj.gz']
                output_folder_path = tempfile.gettempdir()
                compressed_file_path = os.path.join(output_folder_path, "mesh_t_1.obj.gz")
                decompressed_file_path = os.path.join(output_folder_path, "mesh_t_1.obj")

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
                
                # Import the decompressed OBJ model into Blender
                try:
                    self.import_model(decompressed_file_path)
                    self.report({'INFO'}, "3D model successfully imported.")
                except Exception as e:
                    self.report({'ERROR'}, f"Failed to import model: {str(e)}")
            else:
                self.report({'ERROR'}, "Error: Compressed file 'mesh_t_1.obj.gz' not found in the response.")

        else:
            self.report({'ERROR'}, f"Failed to receive a valid response. Status code: {response.status_code}")

        self._is_running = False
        context.scene.make3d_is_running = False

    def send_to_comfy_server(self, image_data, prompt, timeout_minutes):
        scene = bpy.context.scene

        if scene.make3d_use_hosted_server:
            url = SERVER_URL
            headers = {}
            # headers = {"Authorization": f"Bearer {scene.make3d_api_key}"}
        else:
            url = f"{scene.make3d_local_address}/generate-3d"
            headers = {}

        files = {
            'image': ('image.png', image_data, 'image/png')
        }

        data = {
            'prompt': prompt,
            'timeout': timeout_minutes * 60  # Convert minutes to seconds
        }

        return requests.post(url, files=files, data=data, headers=headers, timeout=timeout_minutes * 60 + 60)


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
        
    
    def import_model(self, model_path):
        ext = os.path.splitext(model_path)[1].lower()
        if ext == '.obj':
            bpy.ops.import_scene.obj(filepath=model_path)
        elif ext == '.fbx':
            bpy.ops.import_scene.fbx(filepath=model_path)
        elif ext == '.gltf' or ext == '.glb':
            bpy.ops.import_scene.gltf(filepath=model_path)
        else:
            raise Exception(f"Unsupported model format: {ext}")


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


class MAKE3D_OT_settings(bpy.types.Operator):
    bl_idname = "make3d.settings"
    bl_label = "3D Generation Settings"
    bl_options = {'REGISTER', 'INTERNAL'}

    use_hosted_server = bpy.props.BoolProperty(
        name="Use Hosted Server",
        description="Use a hosted Comfy server instead of a local one",
        default=True
    )

    api_key = bpy.props.StringProperty(
        name="API Key",
        description="API Key for the hosted server"
    )

    local_address = bpy.props.StringProperty(
        name="Local Server Address",
        description="Address of the local Comfy server",
        default="http://127.0.0.1:8188"
    )

    timeout_minutes = bpy.props.IntProperty(
        name="Timeout (Minutes)",
        description="Maximum time to wait for server response",
        default=10,
        min=1,
        max=30
    )

    def execute(self, context):
        scene = context.scene
        scene.make3d_use_hosted_server = self.use_hosted_server
        scene.make3d_api_key = self.api_key
        scene.make3d_local_address = self.local_address
        scene.make3d_timeout_minutes = self.timeout_minutes
        return {'FINISHED'}

    def invoke(self, context, event):
        scene = context.scene
        self.use_hosted_server = scene.make3d_use_hosted_server
        self.api_key = scene.make3d_api_key
        self.local_address = scene.make3d_local_address
        self.timeout_minutes = scene.make3d_timeout_minutes
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "use_hosted_server")
        if self.use_hosted_server:
            layout.prop(self, "api_key")
        else:
            layout.prop(self, "local_address")
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
    bpy.utils.register_class(MAKE3D_PT_panel)
    bpy.utils.register_class(MAKE3D_OT_settings)
    bpy.utils.register_class(MAKE3D_OT_cancel)

    bpy.types.Scene.make3d_image_path = bpy.props.StringProperty(
        name="Image Path",
        description="Path to the input image",
        subtype='FILE_PATH'
    )

    bpy.types.Scene.make3d_use_hosted_server = bpy.props.BoolProperty(
        name="Use Hosted Server",
        description="Use the hosted Comfy server instead of a local one",
        default=True
    )
    bpy.types.Scene.make3d_api_key = bpy.props.StringProperty(
        name="API Key",
        description="API Key for the hosted server"
    )
    bpy.types.Scene.make3d_local_address = bpy.props.StringProperty(
        name="Local Server Address",
        description="Address of the local Comfy server",
        default="http://127.0.0.1:8188"
    )
    bpy.types.Scene.make3d_timeout_minutes = bpy.props.IntProperty(
        name="Timeout (Minutes)",
        description="Maximum time to wait for server response",
        default=10,
        min=1,
        max=30
    )
    bpy.types.Scene.make3d_is_running = bpy.props.BoolProperty(default=False)


def unregister():
    bpy.utils.unregister_class(MAKE3D_OT_generate_model)
    bpy.utils.unregister_class(MAKE3D_OT_select_image)
    bpy.utils.unregister_class(MAKE3D_PT_panel)
    bpy.utils.unregister_class(MAKE3D_OT_settings)
    bpy.utils.unregister_class(MAKE3D_OT_cancel)

    del bpy.types.Scene.make3d_image_path
    del bpy.types.Scene.make3d_use_hosted_server
    del bpy.types.Scene.make3d_api_key
    del bpy.types.Scene.make3d_local_address
    del bpy.types.Scene.make3d_timeout_minutes
    del bpy.types.Scene.make3d_is_running

if __name__ == "__main__":
    register()
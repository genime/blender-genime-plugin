import bpy
import requests
import tempfile
import os
import base64
import threading
import uuid

bl_info = {
    "name": "In-betweening Generator",
    "author": "Your Name",
    "version": (1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > In-betweening",
    "description": "Generate in-between frames using an external API",
    "category": "Animation",
}

class INBETWEEN_OT_generate(bpy.types.Operator):
    bl_idname = "inbetween.generate"
    bl_label = "Generate In-betweens"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _thread = None
    _is_running = False

    @classmethod
    def poll(cls, context):
        return not context.scene.inbetween_is_running

    def execute(self, context):
        context.scene.inbetween_is_running = True
        self._is_running = True
        self._thread = threading.Thread(target=self.generate_inbetweens, args=(context,))
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
        context.scene.inbetween_is_running = False
        self.report({'INFO'}, "In-betweening process cancelled")

    def generate_inbetweens(self, context):
        scene = context.scene
        start_frame = scene.inbetween_start_frame
        end_frame = scene.inbetween_end_frame
        prompt = scene.prompt
        eta = scene.eta
        cfg_scale = scene.cfg_scale
        sampling_steps = scene.sampling_steps
        frame_stride = scene.frame_stride
        width = scene.width
        height = scene.height

        # Convert frames to PNG
        start_image = self.frame_to_png(start_frame)
        end_image = self.frame_to_png(end_frame)

        # Send images to API
        response = self.send_to_api(
            start_image,
            end_image,
            prompt,
            eta,
            cfg_scale,
            sampling_steps,
            frame_stride,
            width,
            height
        )

        if response.status_code == 200:
            response_data = response.json()
            inbetween_frames = response_data['frames']
            width = response_data['width']
            height = response_data['height']

            # Set render resolution to match the inbetween frames
            scene.render.resolution_x = width
            scene.render.resolution_y = height

            output_dir = self.create_unique_directory(scene.inbetween_output_dir)

            self.insert_inbetween_frames(inbetween_frames, start_frame, output_dir)
            self.ensure_gp_visibility()
            self.report({'INFO'}, f"Generated {len(inbetween_frames)} in-between frames")
        else:
            self.report({'ERROR'}, f"Failed to generate in-betweens. Status code: {response.status_code}")

        self._is_running = False
        context.scene.inbetween_is_running = False

    def create_unique_directory(self, save_folder):
        unique_id = uuid.uuid4()
        unique_dir_name = f"genime_bf_{unique_id}"
        unique_dir_path = os.path.join(save_folder, unique_dir_name)
        try:
            os.makedirs(unique_dir_path)
            return unique_dir_path
        except OSError:
            return save_folder

    def frame_to_png(self, frame):
        scene = bpy.context.scene
        scene.frame_set(frame)

        # Create a temporary file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_filename = temp_file.name

        # Save render result to the temporary file
        bpy.ops.render.render()
        bpy.data.images['Render Result'].save_render(temp_filename)

        # Read the file content
        with open(temp_filename, 'rb') as file:
            image_data = file.read()

        # Delete the temporary file
        os.unlink(temp_filename)

        return image_data

    def send_to_api(self, start_image, end_image, prompt, eta, cfg_scale, sampling_steps, frame_stride, width, height):
        scene = bpy.context.scene

        if scene.inbetween_use_hosted_model:
            url = "https://genime-production.up.railway.app/frame-inbetween"
            headers = {"X-API-KEY": scene.inbetween_api_key}
        else:
            url = f"{scene.inbetween_local_address}/frame-inbetween"
            headers = {}

        files = {
            'image1': ('image1.png', start_image),
            'image2': ('image2.png', end_image)
        }

        data = {
            'prompt': prompt,
            'eta': eta,
            'cfg_scale': cfg_scale,
            'steps': sampling_steps,
            'width': width,
            'height': height,
            'frame_stride': frame_stride
        }

        return requests.post(url, files=files, data=data, headers=headers, timeout=1000)

    def insert_inbetween_frames(self, inbetween_frames, start_frame, output_dir):
        self.report({'INFO'}, f"Starting to insert {len(inbetween_frames)} frames")

        for i, frame_data in enumerate(inbetween_frames):
            try:
                frame_number = start_frame + i
                self.report({'INFO'}, f"Inserting frame {i+1} of {len(inbetween_frames)}")
                self.insert_frame(frame_data, frame_number, output_dir)
                self.report({'INFO'}, f"Successfully inserted frame {i+1}")
            except Exception as e:
                self.report({'ERROR'}, f"Error inserting frame {i+1}: {str(e)}")
                break

        try:
            # Ensure all strips are visible in the sequence editor
            for strip in bpy.context.scene.sequence_editor.sequences_all:
                strip.mute = False

            bpy.context.view_layer.update()
            self.report({'INFO'}, "Finished inserting frames and updating view layer")
        except Exception as e:
            self.report({'ERROR'}, f"Error after inserting frames: {str(e)}")

    def ensure_gp_visibility(self):
        gp_object = bpy.data.objects.get("InbetweenAnimation")
        if gp_object:
            gp_object.hide_viewport = False
            gp_object.hide_render = False
            gp_object.hide_select = False

            # Ensure it's in the active collection
            if gp_object.name not in bpy.context.collection.objects:
                bpy.context.collection.objects.link(gp_object)

            # Make it the active object
            bpy.context.view_layer.objects.active = gp_object

            # Select it
            gp_object.select_set(True)

            # Set the viewport to look at the object
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    for region in area.regions:
                        if region.type == 'WINDOW':
                            override = {'area': area, 'region': region}
                            bpy.ops.view3d.view_selected(override)
                            break
                    break


    def insert_frame(self, frame_data, frame_number, output_dir):
        try:
            self.report({'INFO'}, f"Starting to insert frame {frame_number}")
            scene = bpy.context.scene
            scene.frame_set(frame_number)

            if not output_dir:
                self.report({'ERROR'}, "Please select an output directory first")
                return {'CANCELLED'}

            path = os.path.join(output_dir, f"frame_{frame_number}.png")

            # Decode the base64 string to bytes
            decoded_data = base64.b64decode(frame_data)

            # Create a temporary file
            with open(path, 'wb') as file:
                file.write(decoded_data)

            # Create a new Blender image
            image_name = f"Inbetween_{frame_number}"
            new_image = bpy.data.images.load(path)
            new_image.name = image_name

            # Ensure we have a sequence editor
            if not scene.sequence_editor:
                scene.sequence_editor_create()

            # Create a new image strip
            strip = scene.sequence_editor.sequences.new_image(
                name=image_name,
                filepath=path,
                channel=1,
                frame_start=frame_number
            )

            # Set the duration
            strip.frame_final_duration = 1  # Set duration to 1 frame

            # Update the scene
            bpy.context.view_layer.update()

            self.report({'INFO'}, path)
            self.report({'INFO'}, f"Successfully inserted frame {frame_number}")
        except Exception as e:
            self.report({'ERROR'}, f"Error in insert_frame for frame {frame_number}: {str(e)}")
            raise  # Re-raise the exception to be caught in insert_inbetween_frames

class INBETWEEN_OT_settings(bpy.types.Operator):
    bl_idname = "inbetween.settings"
    bl_label = "In-betweening Settings"
    bl_options = {'REGISTER', 'INTERNAL'}

    use_hosted_model: bpy.props.BoolProperty(
        name="Use Hosted Model",
        description="Use the hosted model instead of running locally",
        default=True
    )

    api_key: bpy.props.StringProperty(
        name="X-API-KEY",
        description="API Key for the hosted model"
    )

    local_address: bpy.props.StringProperty(
        name="Local Server Address",
        description="Address of the local server",
        default="http://127.0.0.1:8188"
    )

    def execute(self, context):
        scene = context.scene
        scene.inbetween_use_hosted_model = self.use_hosted_model
        scene.inbetween_api_key = self.api_key
        scene.inbetween_local_address = self.local_address
        return {'FINISHED'}

    def invoke(self, context, event):
        scene = context.scene
        self.use_hosted_model = scene.inbetween_use_hosted_model
        self.api_key = scene.inbetween_api_key
        self.local_address = scene.inbetween_local_address
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "use_hosted_model")
        if self.use_hosted_model:
            layout.prop(self, "api_key")
        else:
            layout.prop(self, "local_address")

class INBETWEEN_OT_cancel(bpy.types.Operator):
    bl_idname = "inbetween.cancel"
    bl_label = "Cancel In-betweening"
    bl_description = "Cancel the ongoing in-betweening process"

    def execute(self, context):
        if context.scene.inbetween_is_running:
            context.scene.inbetween_is_running = False
            self.report({'INFO'}, "Cancelling in-betweening process...")
        return {'FINISHED'}

class INBETWEEN_PT_panel(bpy.types.Panel):
    bl_label = "In-betweening Generator"
    bl_idname = "INBETWEEN_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'In-betweening'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.operator("inbetween.settings", text="Settings", icon='PREFERENCES')

        layout.prop(scene, "inbetween_start_frame")
        layout.prop(scene, "inbetween_end_frame")
        layout.prop(scene, "prompt")
        layout.prop(scene, "eta")
        layout.prop(scene, "cfg_scale")
        layout.prop(scene, "sampling_steps")
        layout.prop(scene, "frame_stride")
        layout.prop(scene, "width")
        layout.prop(scene, "height")
        layout.prop(scene, "inbetween_output_dir")
        if scene.inbetween_is_running:
            layout.label(text="Generating in-betweens...", icon='RENDER_STILL')
            layout.operator("inbetween.cancel", text="Cancel", icon='CANCEL')
        else:
            layout.operator("inbetween.generate")

def register():
    bpy.utils.register_class(INBETWEEN_OT_generate)
    bpy.utils.register_class(INBETWEEN_PT_panel)
    bpy.utils.register_class(INBETWEEN_OT_settings)
    bpy.utils.register_class(INBETWEEN_OT_cancel)
    bpy.types.Scene.inbetween_start_frame = bpy.props.IntProperty(name="Start Frame", default=1)
    bpy.types.Scene.inbetween_end_frame = bpy.props.IntProperty(name="End Frame", default=10)
    bpy.types.Scene.prompt = bpy.props.StringProperty(name="Prompt")
    bpy.types.Scene.eta = bpy.props.FloatProperty(name="ETA", default=1, min=0, max=1)
    bpy.types.Scene.cfg_scale= bpy.props.FloatProperty(name="CFG Scale", default=7.5, min=1, max=15)
    bpy.types.Scene.sampling_steps = bpy.props.IntProperty(name="Sampling Steps", default=50, min=1, max=60)
    bpy.types.Scene.frame_stride = bpy.props.IntProperty(name="Frame Stride", default=10, min=1, max=30)
    bpy.types.Scene.width = bpy.props.IntProperty(name="Width", default=512, min=64, max=5000)
    bpy.types.Scene.height = bpy.props.IntProperty(name="Height", default=320, min=64, max=5000)
    bpy.types.Scene.inbetween_output_dir = bpy.props.StringProperty(
        name="Output Directory",
        description="Directory to save in-between frames",
        subtype='DIR_PATH'
    )
    bpy.types.Scene.inbetween_use_hosted_model = bpy.props.BoolProperty(
        name="Use Hosted Model",
        description="Use the hosted model instead of running locally",
        default=True
    )
    bpy.types.Scene.inbetween_api_key = bpy.props.StringProperty(
        name="X-API-KEY",
        description="API Key for the hosted model"
    )
    bpy.types.Scene.inbetween_local_address = bpy.props.StringProperty(
        name="Local Server Address",
        description="Address of the local server",
        default="http://127.0.0.1:8080"
    )
    bpy.types.Scene.inbetween_is_running = bpy.props.BoolProperty(default=False)

def unregister():
    bpy.utils.unregister_class(INBETWEEN_OT_generate)
    bpy.utils.unregister_class(INBETWEEN_PT_panel)
    bpy.utils.unregister_class(INBETWEEN_OT_settings)
    bpy.utils.unregister_class(INBETWEEN_OT_cancel)
    del bpy.types.Scene.inbetween_start_frame
    del bpy.types.Scene.inbetween_end_frame
    del bpy.types.Scene.prompt
    del bpy.types.Scene.eta
    del bpy.types.Scene.cfg_scale
    del bpy.types.Scene.sampling_steps
    del bpy.types.Scene.frame_stride
    del bpy.types.Scene.width
    del bpy.types.Scene.height
    del bpy.types.Scene.inbetween_output_dir
    del bpy.types.Scene.inbetween_use_hosted_model
    del bpy.types.Scene.inbetween_api_key
    del bpy.types.Scene.inbetween_local_address
    del bpy.types.Scene.inbetween_is_running

if __name__ == "__main__":
    register()
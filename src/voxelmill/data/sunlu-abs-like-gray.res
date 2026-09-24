schema_version = 1

[resin]
id = "sunlu-abs-like-gray"
name = "Sunlu ABS-like gray"

[processes.mars5-ultra.process]
layer_height_mm = 0.05
bottom_exposure_s = 35.0
normal_exposure_s = 3.5
bottom_layers = 4
transition_layers = 5
bottom_rest_after_exposure_s = 1.0
normal_rest_after_exposure_s = 1.0
bottom_settle_before_exposure_s = 0.5
normal_settle_before_exposure_s = 0.5
bottom_wait_after_lift_s = 0.0
normal_wait_after_lift_s = 0.0

[processes.mars5-ultra.support]
spacing_mm = 3.0
contact_diameter_mm = 0.35
penetration_mm = 0.2
pillar_diameter_mm = 0.9
tip_length_mm = 2.0
raft_thickness_mm = 1.0
raft_expansion_mm = 2.0

import SimpleITK as sitk
import numpy as np
import pickle
import matplotlib.pyplot as plt
import cv2
import perf_log
from skimage.filters import sobel, gaussian, difference_of_gaussians


def _emit_log(message):
    print(f"LOG: {message}", flush=True)


def match_histograms(to_match, match_to):
    """
    Match the to_match histogram to the match_to using sitk
    Args:
        to_match (sitk.Image): The image to be matched.
        match_to (sitk.Image): The image to be matched to.
    Returns:
        sitk.Image: The matched image.
    """
    # make sure fixed and moving are sitk images
    matcher = sitk.HistogramMatchingImageFilter()
    matcher.SetNumberOfHistogramLevels(1024)
    matcher.SetNumberOfMatchPoints(10)
    matcher.ThresholdAtMeanIntensityOn()
    return matcher.Execute(to_match, match_to)


def preprocess_image(image):
    """
    Preprocess the image to enhance features.
    """
    # Convert SimpleITK image to numpy array
    image_array = sitk.GetArrayFromImage(sitk.Cast(image, sitk.sitkUInt8))
    blurred = cv2.GaussianBlur(image_array, (5, 5), 0)
    edges = sobel(blurred)
    edge_min, edge_max = edges.min(), edges.max()
    if edge_max > edge_min:
        edges = (edges - edge_min) / (edge_max - edge_min)
    else:
        edges = np.zeros_like(edges)
        _emit_log("preprocess_flat_edges normalized to zeros")
    edges = edges.astype(np.float32)
    edges = sitk.GetImageFromArray(edges)

    return edges


def _image_has_registration_signal(image, min_nonzero_frac=0.01, min_std=1.0):
    """Return True when the image has enough contrast for registration."""
    arr = sitk.GetArrayFromImage(sitk.Cast(image, sitk.sitkUInt8))
    if arr.size == 0:
        return False
    nonzero_frac = float(np.count_nonzero(arr)) / float(arr.size)
    if nonzero_frac < min_nonzero_frac:
        return False
    if float(np.std(arr)) < min_std:
        return False
    return True


def _configure_registration_method(registration, fixed_metric_mask=None):
    registration.SetMetricAsMattesMutualInformation()
    registration.SetMetricSamplingPercentage(0.25)
    registration.SetOptimizerScalesFromPhysicalShift()
    registration.SetInterpolator(sitk.sitkLinear)
    if fixed_metric_mask is not None:
        registration.SetMetricFixedMask(fixed_metric_mask)


def _numpy_mask_to_sitk(mask_uint8, reference_image):
    mask_arr = (mask_uint8 >= 128).astype(np.uint8)
    sitk_mask = sitk.GetImageFromArray(mask_arr)
    sitk_mask.CopyInformation(reference_image)
    return sitk_mask


def _execute_registration_stage(
    fixed_f32,
    moving_f32,
    configure_fn,
    stage_name,
    geometry_fallback,
    fixed_metric_mask=None,
):
    """Run one registration stage with edge, float, and geometry-only fallbacks."""
    with perf_log.perf_section(f"align.register.{stage_name}.preprocess"):
        fixed_edge = preprocess_image(fixed_f32)
        moving_edge = preprocess_image(moving_f32)
    attempts = [
        (fixed_edge, moving_edge, "edge"),
        (fixed_f32, moving_f32, "float"),
    ]
    last_error = None
    for fixed_img, moving_img, label in attempts:
        registration = sitk.ImageRegistrationMethod()

        def configure(registration, _configure_fn=configure_fn):
            _configure_fn(registration)
            if fixed_metric_mask is not None:
                registration.SetMetricFixedMask(fixed_metric_mask)

        try:
            configure(registration)
            with perf_log.perf_section(
                f"align.register.{stage_name}.{label}"
            ):
                result = registration.Execute(fixed_img, moving_img)
            if perf_log.perf_enabled():
                _emit_log(
                    "align_register_stage "
                    f"stage={stage_name} input={label} "
                    f"iterations={registration.GetOptimizerIteration()} "
                    f"metric={registration.GetMetricValue():.8g}"
                )
            return result
        except RuntimeError as exc:
            last_error = exc
            _emit_log(
                f"registration_{stage_name}_{label}_failed error={exc}"
            )
    _emit_log(
        f"registration_{stage_name}_geometry_only_fallback"
        + (f" last_error={last_error}" if last_error else "")
    )
    return geometry_fallback()


def multimodal_registration(fixed, moving, fixed_metric_mask=None):
    fixed_f32 = sitk.Cast(fixed, sitk.sitkFloat32)
    moving_f32 = sitk.Cast(moving, sitk.sitkFloat32)

    def configure_rigid(registration):
        rigid_tx = sitk.CenteredTransformInitializer(
            fixed_f32,
            moving_f32,
            sitk.Euler2DTransform(),
            sitk.CenteredTransformInitializerFilter.GEOMETRY,
        )
        _configure_registration_method(registration, fixed_metric_mask)
        registration.SetOptimizerAsGradientDescent(
            learningRate=0.001,
            numberOfIterations=25,
            convergenceMinimumValue=1e-8,
            convergenceWindowSize=20,
        )
        registration.SetShrinkFactorsPerLevel(shrinkFactors=[4, 2, 1])
        registration.SetSmoothingSigmasPerLevel(smoothingSigmas=[3, 2, 0])
        registration.SetInitialTransform(rigid_tx)

    def rigid_geometry_fallback():
        return sitk.CenteredTransformInitializer(
            fixed_f32,
            moving_f32,
            sitk.Euler2DTransform(),
            sitk.CenteredTransformInitializerFilter.GEOMETRY,
        )

    outTx = _execute_registration_stage(
        fixed_f32,
        moving_f32,
        configure_rigid,
        "rigid",
        rigid_geometry_fallback,
        fixed_metric_mask=fixed_metric_mask,
    )

    with perf_log.perf_section("align.register.rigid_resample"):
        rigid_moving = sitk.Resample(
            moving_f32,
            fixed_f32,
            outTx,
            sitk.sitkLinear,
            0.0,
            moving_f32.GetPixelID(),
        )

    def configure_affine(registration):
        initial_tx = sitk.CenteredTransformInitializer(
            fixed_f32, rigid_moving, sitk.AffineTransform(fixed_f32.GetDimension())
        )
        _configure_registration_method(registration, fixed_metric_mask)
        registration.SetOptimizerAsGradientDescent(
            learningRate=0.001,
            numberOfIterations=25,
            convergenceMinimumValue=1e-10,
            convergenceWindowSize=10,
        )
        registration.SetShrinkFactorsPerLevel(shrinkFactors=[4, 2, 1])
        registration.SetSmoothingSigmasPerLevel(smoothingSigmas=[2, 1, 0])
        registration.SetInitialTransform(initial_tx)

    def affine_geometry_fallback():
        return sitk.CenteredTransformInitializer(
            fixed_f32, rigid_moving, sitk.AffineTransform(fixed_f32.GetDimension())
        )

    outTx1 = _execute_registration_stage(
        fixed_f32,
        rigid_moving,
        configure_affine,
        "affine",
        affine_geometry_fallback,
        fixed_metric_mask=fixed_metric_mask,
    )

    with perf_log.perf_section("align.register.affine_resample"):
        resampled_moving = sitk.Resample(
            rigid_moving,
            fixed_f32,
            outTx1,
            sitk.sitkLinear,
            0.0,
            moving_f32.GetPixelID(),
        )

    def configure_bspline(registration):
        transform_domain_mesh_size = [5] * fixed_f32.GetDimension()
        tx = sitk.BSplineTransformInitializer(fixed_f32, transform_domain_mesh_size)
        _configure_registration_method(registration, fixed_metric_mask)
        registration.SetInitialTransform(tx, inPlace=False)
        registration.SetShrinkFactorsPerLevel(shrinkFactors=[4, 2, 1])
        registration.SetSmoothingSigmasPerLevel(smoothingSigmas=[2, 1, 0])
        registration.SetOptimizerAsGradientDescent(
            learningRate=0.0001,
            numberOfIterations=25,
            convergenceMinimumValue=1e-12,
            convergenceWindowSize=20,
        )

    def bspline_geometry_fallback():
        transform_domain_mesh_size = [5] * fixed_f32.GetDimension()
        return sitk.BSplineTransformInitializer(fixed_f32, transform_domain_mesh_size)

    outTx2 = _execute_registration_stage(
        fixed_f32,
        resampled_moving,
        configure_bspline,
        "bspline",
        bspline_geometry_fallback,
        fixed_metric_mask=fixed_metric_mask,
    )

    composite_transform = sitk.CompositeTransform(fixed_f32.GetDimension())
    composite_transform.AddTransform(outTx)
    composite_transform.AddTransform(outTx1)
    composite_transform.AddTransform(outTx2)

    return composite_transform


def resize_image_to_width(image, target_width):
    """
    Resize an image to a target width while maintaining the aspect ratio.

    Parameters:
    - image: The input SimpleITK image.
    - target_width: The desired width of the image after resizing.

    Returns:
    - The resized SimpleITK image.
    """
    # Get the original size and spacing of the image
    original_size = image.GetSize()
    original_spacing = image.GetSpacing()

    # Calculate the new height maintaining the aspect ratio
    aspect_ratio = original_size[1] / original_size[0]
    new_height = int(target_width * aspect_ratio)

    # Calculate the new spacing to maintain the aspect ratio
    new_spacing = [
        original_size[0] / target_width * original_spacing[0],
        original_size[1] / new_height * original_spacing[1],
    ]

    # Set up the resampling filter
    resampler = sitk.ResampleImageFilter()
    resampler.SetSize((target_width, new_height))
    resampler.SetOutputSpacing(new_spacing)
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetInterpolator(sitk.sitkLinear)
    resampler.SetDefaultPixelValue(0)

    # Perform the resampling
    resized_image = resampler.Execute(image)

    return resized_image


def resize_image_nearest_neighbor(input_image, new_size):
    """
    Resize an image using nearest-neighbor interpolation, maintaining the original data type.

    Parameters:
        input_image (SimpleITK.Image): The input image to be resized.
        new_size (tuple or list): The desired size (in pixels) as (width, height,
            [depth]) -- this is forwarded directly to SimpleITK ``SetSize``,
            which uses (x, y, z) ordering. The returned numpy array therefore has
            shape (height, width). Callers must pass (width, height), NOT
            (height, width).

    Returns:
        SimpleITK.Image: The resized image, maintaining the original data type.
    """

    # Calculate the new spacing based on old spacing and old and new sizes
    input_image = sitk.GetImageFromArray(input_image)
    original_size = input_image.GetSize()
    original_spacing = input_image.GetSpacing()
    new_spacing = [
        float(orig_space) * float(orig_size) / float(new_dim)
        for orig_space, orig_size, new_dim in zip(
            original_spacing, original_size, new_size
        )
    ]

    # Set up the resampler with nearest neighbor interpolation, original data type is maintained by default
    resampler = sitk.ResampleImageFilter()
    resampler.SetInterpolator(sitk.sitkNearestNeighbor)
    resampler.SetOutputSpacing(new_spacing)
    resampler.SetOutputPixelType(input_image.GetPixelIDValue())
    resampler.SetSize(new_size)
    resampler.SetOutputOrigin(input_image.GetOrigin())
    resampler.SetOutputDirection(input_image.GetDirection())

    # Apply the resampling operation
    resized_image = resampler.Execute(input_image)
    resized_image = sitk.GetArrayFromImage(resized_image)
    return resized_image


def register_to_atlas(
    tissue,
    section,
    label,
    structure_map_path,
    fixed_keep_mask=None,
    moving_exclude_mask=None,
):
    """
    Register a section to the atlas using sitk.

    Args:
        tissue (numpy.ndarray): The tissue image.
        section (numpy.ndarray): The section image.
        label (numpy.ndarray): The label image.
        class_map_path (str): The path to the class map pickle file.

    Returns:
        numpy.ndarray: The registered label image.
        numpy.ndarray: The registered atlas image.
        numpy.ndarray: The color label image.
    """

    with perf_log.perf_section("align.register.load_structure_map"):
        with open(structure_map_path, "rb") as f:
            structure_map = pickle.load(f)

    section_work = np.array(section, copy=True)
    label_work = np.array(label, copy=True)
    if moving_exclude_mask is not None:
        exclude = moving_exclude_mask.astype(bool)
        if exclude.shape != section_work.shape:
            exclude = cv2.resize(
                exclude.astype(np.uint8),
                (section_work.shape[1], section_work.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
        section_work[exclude] = 0
        label_work[exclude] = 0

    tissue_resized = cv2.resize(tissue, (360, 360))
    section_resized = cv2.resize(section_work, (360, 360))
    label = resize_image_nearest_neighbor(label_work, (360, 360))
    fixed = sitk.GetImageFromArray(tissue_resized, isVector=False)

    fixed_metric_mask = None
    if fixed_keep_mask is not None:
        keep = fixed_keep_mask
        if keep.shape != tissue.shape:
            keep = cv2.resize(
                keep,
                (tissue.shape[1], tissue.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        keep_resized = cv2.resize(keep, (360, 360), interpolation=cv2.INTER_NEAREST)
        fixed_metric_mask = _numpy_mask_to_sitk(keep_resized, fixed)

    # Vectorized layer-specific intensity adjustment
    label_flat = label.ravel()
    section_flat = section_resized.ravel().astype(np.int16)
    layer4_mask = np.zeros(label_flat.shape, dtype=bool)
    layer5_mask = np.zeros(label_flat.shape, dtype=bool)
    for region_id, info in structure_map.items():
        name_lower = info["name"].lower()
        region_mask = label_flat == region_id
        if "layer 4" in name_lower:
            layer4_mask |= region_mask
        elif "layer 5" in name_lower:
            layer5_mask |= region_mask
    section_flat[layer4_mask] = np.clip(section_flat[layer4_mask] + 15, 0, 255)
    section_flat[layer5_mask] = np.clip(section_flat[layer5_mask] - 7, 0, 255)
    section_resized = section_flat.reshape(section_resized.shape).astype(np.uint8)

    moving = sitk.GetImageFromArray(section_resized, isVector=False)
    label = sitk.GetImageFromArray(label, isVector=False)
    fixed = match_histograms(fixed, moving)
    if not _image_has_registration_signal(fixed):
        raise ValueError(
            "Insufficient registration signal in atlas/tissue image (blank or uniform)"
        )
    if not _image_has_registration_signal(moving):
        raise ValueError(
            "Insufficient registration signal in section image (blank or uniform)"
        )
    # cast to float 32
    fixed = sitk.Cast(fixed, sitk.sitkFloat32)
    moving = sitk.Cast(moving, sitk.sitkFloat32)
    tx = multimodal_registration(fixed, moving, fixed_metric_mask=fixed_metric_mask)

    resampler = sitk.ResampleImageFilter()
    resampler.SetReferenceImage(fixed)
    resampler.SetInterpolator(sitk.sitkNearestNeighbor)
    resampler.SetTransform(tx)
    resampler.SetOutputPixelType(sitk.sitkUInt32)
    resampler.SetDefaultPixelValue(0)
    with perf_log.perf_section("align.register.final_resample"):
        resampled_label = resampler.Execute(label)
        resampler.SetOutputPixelType(sitk.sitkUInt8)
        resampled_atlas = resampler.Execute(moving)
    color_label = np.zeros(
        (resampled_label.GetSize()[1], resampled_label.GetSize()[0], 3), dtype=np.uint8
    )

    with perf_log.perf_section("align.register.colorize_and_resize"):
        label_array = sitk.GetArrayFromImage(resampled_label)
        for region_id, info in structure_map.items():
            mask = label_array == region_id
            if np.any(mask):
                color_label[mask] = info["color"]

        # convert color label to cv2
        color_label = cv2.cvtColor(color_label, cv2.COLOR_RGB2BGR)
        resampled_label = sitk.GetArrayFromImage(resampled_label)
        resampled_atlas = sitk.GetArrayFromImage(resampled_atlas)
        # resize atlas back to original size
        resampled_atlas = cv2.resize(resampled_atlas, tissue.shape[:2][::-1])
        color_label = cv2.resize(color_label, tissue.shape[:2][::-1])
        # convert color label back to rgb
        color_label = cv2.cvtColor(color_label, cv2.COLOR_BGR2RGB)
        resampled_label = resize_image_nearest_neighbor(
            resampled_label, tissue.shape[:2][::-1]
        )

    if fixed_keep_mask is not None:
        keep_full = fixed_keep_mask
        if keep_full.shape != resampled_label.shape:
            keep_full = cv2.resize(
                keep_full,
                (resampled_label.shape[1], resampled_label.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        off = keep_full < 128
        resampled_label = resampled_label.copy()
        resampled_label[off] = 0
        color_label = color_label.copy()
        color_label[off] = 0

    return resampled_label, resampled_atlas, color_label

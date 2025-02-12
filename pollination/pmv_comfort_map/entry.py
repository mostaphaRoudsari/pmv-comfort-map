from pollination_dsl.dag import Inputs, DAG, task, Outputs
from dataclasses import dataclass
from typing import Dict, List

# pollination plugins and recipes
from pollination.ladybug.translate import EpwToWea
from pollination.lbt_honeybee.edit import ModelModifiersFromConstructions

from pollination.honeybee_energy.settings import SimParComfort, DynamicOutputs
from pollination.honeybee_energy.simulate import SimulateModel
from pollination.honeybee_energy.translate import ModelOccSchedules

from pollination.honeybee_radiance.sun import CreateSunMatrix, ParseSunUpHours
from pollination.honeybee_radiance.translate import CreateRadianceFolderGrid
from pollination.honeybee_radiance.octree import CreateOctree, CreateOctreeWithSky, \
    CreateOctreeAbstractedGroups
from pollination.honeybee_radiance.sky import CreateSkyDome, CreateSkyMatrix
from pollination.honeybee_radiance.grid import SplitGridFolder, MergeFolderData
from pollination.honeybee_radiance.viewfactor import ViewFactorModifiers

from pollination.ladybug_comfort.map import MapResultInfo
from pollination.path.copy import CopyMultiple

# input/output alias
from pollination.alias.inputs.model import hbjson_model_grid_room_input
from pollination.alias.inputs.ddy import ddy_input
from pollination.alias.inputs.simulation import additional_idf_input
from pollination.alias.inputs.comfort import air_speed_input, met_rate_input, \
    clo_value_input, pmv_comfort_par_input, solar_body_par_indoor_input
from pollination.alias.inputs.north import north_input
from pollination.alias.inputs.bool_options import write_set_map_input
from pollination.alias.inputs.runperiod import run_period_input
from pollination.alias.inputs.radiancepar import rad_par_annual_input
from pollination.alias.inputs.grid import min_sensor_count_input, cpu_count
from pollination.alias.outputs.comfort import tcp_output, hsp_output, csp_output, \
    thermal_condition_output, operative_or_set_output, pmv_output, env_conditions_output

from ._radiance import RadianceMappingEntryPoint
from ._comfort import ComfortMappingEntryPoint
from ._dynamic import DynamicContributionEntryPoint


@dataclass
class PmvComfortMapEntryPoint(DAG):
    """PMV comfort map entry point."""

    # inputs
    model = Inputs.file(
        description='A Honeybee model in HBJSON file format.',
        extensions=['json', 'hbjson'],
        alias=hbjson_model_grid_room_input
    )

    epw = Inputs.file(
        description='EPW weather file to be used for the comfort map simulation.',
        extensions=['epw']
    )

    ddy = Inputs.file(
        description='A DDY file with design days to be used for the initial '
        'sizing calculation.', extensions=['ddy'],
        alias=ddy_input
    )

    north = Inputs.float(
        default=0,
        description='A a number between -360 and 360 for the counterclockwise '
        'difference between the North and the positive Y-axis in degrees.',
        spec={'type': 'number', 'minimum': -360, 'maximum': 360},
        alias=north_input
    )

    run_period = Inputs.str(
        description='An AnalysisPeriod string to set the start and end dates of '
        'the simulation (eg. "6/21 to 9/21 between 0 and 23 @1"). If None, '
        'the simulation will be annual.', default='', alias=run_period_input
    )

    additional_idf = Inputs.file(
        description='An IDF file with text to be appended before simulation. This '
        'input can be used to include EnergyPlus objects that are not '
        'currently supported by honeybee.', extensions=['idf'],
        optional=True, alias=additional_idf_input
    )

    cpu_count = Inputs.int(
        default=50,
        description='The maximum number of CPUs for parallel execution. This will be '
        'used to determine the number of sensors run by each worker.',
        spec={'type': 'integer', 'minimum': 1},
        alias=cpu_count
    )

    min_sensor_count = Inputs.int(
        description='The minimum number of sensors in each sensor grid after '
        'redistributing the sensors based on cpu_count. This value takes '
        'precedence over the cpu_count and can be used to ensure that '
        'the parallelization does not result in generating unnecessarily small '
        'sensor grids. The default value is set to 1, which means that the '
        'cpu_count is always respected.', default=1,
        spec={'type': 'integer', 'minimum': 1},
        alias=min_sensor_count_input
    )

    write_set_map = Inputs.str(
        description='A switch to note whether the output temperature CSV should '
        'record Operative Temperature or Standard Effective Temperature (SET). '
        'SET is relatively intense to compute and so only recording Operative '
        'Temperature can greatly reduce run time, particularly when air speeds '
        'are low. However, SET accounts for all 6 PMV model inputs and so is a '
        'more representative "feels-like" temperature for the PMV model.',
        default='write-op-map', alias=write_set_map_input,
        spec={'type': 'string', 'enum': ['write-op-map', 'write-set-map']}
    )

    air_speed = Inputs.file(
        description='A CSV file containing a single number for air speed in m/s or '
        'several rows of air speeds that align with the length of the run period. This '
        'will be used for all indoor comfort evaluation.', extensions=['txt', 'csv'],
        optional=True, alias=air_speed_input
    )

    met_rate = Inputs.file(
        description='A CSV file containing a single number for metabolic rate in met '
        'or several rows of met values that align with the length of the run period.',
        extensions=['txt', 'csv'], optional=True, alias=met_rate_input
    )

    clo_value = Inputs.file(
        description='A CSV file containing a single number for clothing level in clo '
        'or several rows of clo values that align with the length of the run period.',
        extensions=['txt', 'csv'], optional=True, alias=clo_value_input
    )

    solarcal_parameters = Inputs.str(
        description='A SolarCalParameter string to customize the assumptions of '
        'the SolarCal model.', default='--posture seated --sharp 135 '
        '--absorptivity 0.7 --emissivity 0.95',
        alias=solar_body_par_indoor_input
    )

    comfort_parameters = Inputs.str(
        description='An PMVParameter string to customize the assumptions of '
        'the PMV comfort model.', default='--ppd-threshold 10',
        alias=pmv_comfort_par_input
    )

    radiance_parameters = Inputs.str(
        description='Radiance parameters for ray tracing.',
        default='-ab 2 -ad 5000 -lw 2e-05',
        alias=rad_par_annual_input
    )

    # tasks
    @task(template=SimParComfort)
    def create_sim_par(self, ddy=ddy, run_period=run_period, north=north) -> List[Dict]:
        return [
            {
                'from': SimParComfort()._outputs.sim_par_json,
                'to': 'energy/simulation_parameter.json'
            }
        ]

    @task(template=DynamicOutputs)
    def dynamic_construction_outputs(
        self, model=model, base_idf=additional_idf
    ) -> List[Dict]:
        return [
            {
                'from': DynamicOutputs()._outputs.dynamic_out_idf,
                'to': 'energy/additional.idf'
            }
        ]

    @task(template=SimulateModel, needs=[create_sim_par, dynamic_construction_outputs])
    def run_energy_simulation(
        self, model=model, epw=epw, sim_par=create_sim_par._outputs.sim_par_json,
        additional_idf=dynamic_construction_outputs._outputs.dynamic_out_idf
    ) -> List[Dict]:
        return [
            {'from': SimulateModel()._outputs.sql, 'to': 'energy/eplusout.sql'},
            {'from': SimulateModel()._outputs.idf, 'to': 'energy/in.idf'}
        ]

    @task(template=EpwToWea)
    def create_wea(self, epw=epw, period=run_period) -> List[Dict]:
        return [
            {
                'from': EpwToWea()._outputs.wea,
                'to': 'radiance/shortwave/in.wea'
            }
        ]

    @task(template=CreateSunMatrix, needs=[create_wea])
    def generate_sunpath(self, north=north, wea=create_wea._outputs.wea, output_type=1):
        """Create sunpath for sun-up-hours."""
        return [
            {
                'from': CreateSunMatrix()._outputs.sunpath,
                'to': 'radiance/shortwave/resources/sunpath.mtx'
            },
            {
                'from': CreateSunMatrix()._outputs.sun_modifiers,
                'to': 'radiance/shortwave/resources/suns.mod'
            }
        ]

    @task(template=CreateSkyDome)
    def create_sky_dome(self):
        """Create sky dome for daylight coefficient studies."""
        return [
            {
                'from': CreateSkyDome()._outputs.sky_dome,
                'to': 'radiance/shortwave/resources/sky.dome'
            }
        ]

    @task(template=CreateSkyMatrix, needs=[create_wea])
    def create_total_sky(
        self, north=north, wea=create_wea._outputs.wea,
        sky_type='total', output_type='solar', sun_up_hours='sun-up-hours'
    ):
        return [
            {
                'from': CreateSkyMatrix()._outputs.sky_matrix,
                'to': 'radiance/shortwave/resources/sky.mtx'
            }
        ]

    @task(template=CreateSkyMatrix, needs=[create_wea])
    def create_direct_sky(
        self, north=north, wea=create_wea._outputs.wea,
        sky_type='sun-only', output_type='solar', sun_up_hours='sun-up-hours'
    ):
        return [
            {
                'from': CreateSkyMatrix()._outputs.sky_matrix,
                'to': 'radiance/shortwave/resources/sky_direct.mtx'
            }
        ]

    @task(template=ParseSunUpHours, needs=[generate_sunpath])
    def parse_sun_up_hours(self, sun_modifiers=generate_sunpath._outputs.sun_modifiers):
        return [
            {
                'from': ParseSunUpHours()._outputs.sun_up_hours,
                'to': 'radiance/shortwave/sun-up-hours.txt'
            }
        ]

    @task(template=ModelModifiersFromConstructions)
    def set_modifiers_from_constructions(
        self, model=model, use_visible='solar', exterior_offset=0.02
    ) -> List[Dict]:
        return [
            {
                'from': ModelModifiersFromConstructions()._outputs.new_model,
                'to': 'radiance/shortwave/model.hbjson'
            }
        ]

    @task(template=CreateRadianceFolderGrid, needs=[set_modifiers_from_constructions])
    def create_rad_folder(
        self, input_model=set_modifiers_from_constructions._outputs.new_model
    ):
        """Translate the input model to a radiance folder."""
        return [
            {
                'from': CreateRadianceFolderGrid()._outputs.model_folder,
                'to': 'radiance/shortwave/model'
            },
            {
                'from': CreateRadianceFolderGrid()._outputs.sensor_grids_file,
                'to': 'results/temperature/grids_info.json'
            },
            {
                'from': CreateRadianceFolderGrid()._outputs.sensor_grids,
                'description': 'Sensor grids information.'
            }
        ]

    @task(template=CopyMultiple, needs=[create_rad_folder])
    def copy_grid_info(self, src=create_rad_folder._outputs.sensor_grids_file):
        return [
            {
                'from': CopyMultiple()._outputs.dst_1,
                'to': 'results/condition/grids_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_2,
                'to': 'results/condition_intensity/grids_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_3,
                'to': 'metrics/TCP/grids_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_4,
                'to': 'metrics/HSP/grids_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_5,
                'to': 'metrics/CSP/grids_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_6,
                'to': 'initial_results/conditions/grids_info.json'
            }
        ]

    @task(
        template=SplitGridFolder, needs=[create_rad_folder],
        sub_paths={'input_folder': 'grid'}
    )
    def split_grid_folder(
        self, input_folder=create_rad_folder._outputs.model_folder,
        cpu_count=cpu_count, cpus_per_grid=3, min_sensor_count=min_sensor_count
    ):
        """Split sensor grid folder based on the number of CPUs"""
        return [
            {
                'from': SplitGridFolder()._outputs.output_folder,
                'to': 'radiance/grid'
            },
            {
                'from': SplitGridFolder()._outputs.dist_info,
                'to': 'initial_results/results/temperature/_redist_info.json'
            },
            {
                'from': SplitGridFolder()._outputs.sensor_grids_file,
                'to': 'radiance/grid/_split_info.json'
            },
            {
                'from': SplitGridFolder()._outputs.sensor_grids,
                'description': 'Sensor grids information.'
            }
        ]

    @task(template=CopyMultiple, needs=[split_grid_folder])
    def copy_redist_info(self, src=split_grid_folder._outputs.dist_info):
        return [
            {
                'from': CopyMultiple()._outputs.dst_1,
                'to': 'initial_results/results/condition/_redist_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_2,
                'to': 'initial_results/results/condition_intensity/_redist_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_3,
                'to': 'initial_results/metrics/TCP/_redist_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_4,
                'to': 'initial_results/metrics/HSP/_redist_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_5,
                'to': 'initial_results/metrics/CSP/_redist_info.json'
            },
            {
                'from': CopyMultiple()._outputs.dst_6,
                'to': 'initial_results/conditions/_redist_info.json'
            }
        ]

    @task(template=CreateOctree, needs=[create_rad_folder])
    def create_octree(self, model=create_rad_folder._outputs.model_folder):
        """Create octree from radiance folder."""
        return [
            {
                'from': CreateOctree()._outputs.scene_file,
                'to': 'radiance/shortwave/resources/scene.oct'
            }
        ]

    @task(
        template=CreateOctreeWithSky, needs=[generate_sunpath, create_rad_folder]
    )
    def create_octree_with_suns(
        self, model=create_rad_folder._outputs.model_folder,
        sky=generate_sunpath._outputs.sunpath
    ):
        """Create octree from radiance folder and sunpath for direct studies."""
        return [
            {
                'from': CreateOctreeWithSky()._outputs.scene_file,
                'to': 'radiance/shortwave/resources/scene_with_suns.oct'
            }
        ]

    @task(
        template=CreateOctreeAbstractedGroups,
        needs=[generate_sunpath, create_rad_folder]
    )
    def create_dynamic_octrees(
        self, model=create_rad_folder._outputs.model_folder,
        sunpath=generate_sunpath._outputs.sunpath
    ):
        """Create a set of octrees for each dynamic window construction."""
        return [
            {
                'from': CreateOctreeAbstractedGroups()._outputs.scene_folder,
                'to': 'radiance/shortwave/resources/dynamic_groups'
            },
            {
                'from': CreateOctreeAbstractedGroups()._outputs.scene_info,
                'description': 'List of octrees to iterate over.'
            }
        ]

    @task(template=ViewFactorModifiers)
    def create_view_factor_modifiers(
        self, model=model, include_sky='include', include_ground='include',
        grouped_shades='grouped'
    ):
        """Create octree from radiance folder and sunpath for direct studies."""
        return [
            {
                'from': ViewFactorModifiers()._outputs.modifiers_file,
                'to': 'radiance/longwave/resources/scene.mod'
            },
            {
                'from': ViewFactorModifiers()._outputs.scene_file,
                'to': 'radiance/longwave/resources/scene.oct'
            }
        ]

    @task(template=ModelOccSchedules)
    def create_model_occ_schedules(self, model=model, period=run_period) -> List[Dict]:
        return [
            {
                'from': ModelOccSchedules()._outputs.occ_schedule_json,
                'to': 'metrics/occupancy_schedules.json'
            }
        ]

    @task(
        template=RadianceMappingEntryPoint,
        needs=[
            create_sky_dome, create_octree_with_suns, create_octree, generate_sunpath,
            create_total_sky, create_direct_sky, create_rad_folder, split_grid_folder,
            create_view_factor_modifiers
        ],
        loop=split_grid_folder._outputs.sensor_grids,
        sub_folder='radiance',
        sub_paths={'sensor_grid': '{{item.full_id}}.pts'}
    )
    def run_radiance_simulation(
        self,
        radiance_parameters=radiance_parameters,
        model=model,
        octree_file_with_suns=create_octree_with_suns._outputs.scene_file,
        octree_file=create_octree._outputs.scene_file,
        octree_file_view_factor=create_view_factor_modifiers._outputs.scene_file,
        grid_name='{{item.full_id}}',
        sensor_grid=split_grid_folder._outputs.output_folder,
        sensor_count='{{item.count}}',
        sky_dome=create_sky_dome._outputs.sky_dome,
        sky_matrix=create_total_sky._outputs.sky_matrix,
        sky_matrix_direct=create_direct_sky._outputs.sky_matrix,
        sun_modifiers=generate_sunpath._outputs.sun_modifiers,
        view_factor_modifiers=create_view_factor_modifiers._outputs.modifiers_file
    ) -> List[Dict]:
        pass

    @task(
        template=DynamicContributionEntryPoint,
        needs=[
            create_sky_dome, generate_sunpath, parse_sun_up_hours,
            create_total_sky, create_direct_sky,
            split_grid_folder, create_dynamic_octrees, run_energy_simulation
        ],
        loop=create_dynamic_octrees._outputs.scene_info,
        sub_folder='radiance',
        sub_paths={
            'octree_file_spec': '{{item.identifier}}/{{item.spec}}',
            'octree_file_diff': '{{item.identifier}}/{{item.diff}}',
            'octree_file_with_suns': '{{item.identifier}}/{{item.sun}}'
        }
    )
    def run_radiance_dynamic_contribution(
        self,
        radiance_parameters=radiance_parameters,
        result_sql=run_energy_simulation._outputs.sql,
        octree_file_spec=create_dynamic_octrees._outputs.scene_folder,
        octree_file_diff=create_dynamic_octrees._outputs.scene_folder,
        octree_file_with_suns=create_dynamic_octrees._outputs.scene_folder,
        group_name='{{item.identifier}}',
        sensor_grid_folder=split_grid_folder._outputs.output_folder,
        sensor_grids=split_grid_folder._outputs.sensor_grids_file,
        sky_dome=create_sky_dome._outputs.sky_dome,
        sky_matrix=create_total_sky._outputs.sky_matrix,
        sky_matrix_direct=create_direct_sky._outputs.sky_matrix,
        sun_modifiers=generate_sunpath._outputs.sun_modifiers,
        sun_up_hours=parse_sun_up_hours._outputs.sun_up_hours,
    ) -> List[Dict]:
        pass

    @task(
        template=ComfortMappingEntryPoint,
        needs=[
            parse_sun_up_hours, create_view_factor_modifiers, create_model_occ_schedules,
            run_energy_simulation, run_radiance_simulation, split_grid_folder,
            run_radiance_dynamic_contribution
        ],
        loop=split_grid_folder._outputs.sensor_grids,
        sub_folder='initial_results',
        sub_paths={
            'enclosure_info': '{{item.full_id}}.json',
            'view_factors': '{{item.full_id}}.csv',
            'indirect_irradiance': '{{item.full_id}}.ill',
            'direct_irradiance': '{{item.full_id}}.ill',
            'ref_irradiance': '{{item.full_id}}.ill'
        }
    )
    def run_comfort_map(
        self,
        epw=epw,
        result_sql=run_energy_simulation._outputs.sql,
        grid_name='{{item.full_id}}',
        enclosure_info='radiance/enclosures',
        view_factors='radiance/longwave/view_factors',
        modifiers=create_view_factor_modifiers._outputs.modifiers_file,
        indirect_irradiance='radiance/shortwave/results/indirect',
        direct_irradiance='radiance/shortwave/results/direct',
        ref_irradiance='radiance/shortwave/results/reflected',
        sun_up_hours=parse_sun_up_hours._outputs.sun_up_hours,
        contributions='radiance/shortwave/dynamic/final/{{item.full_id}}',
        occ_schedules=create_model_occ_schedules._outputs.occ_schedule_json,
        run_period=run_period,
        air_speed=air_speed,
        met_rate=met_rate,
        clo_value=clo_value,
        solarcal_par=solarcal_parameters,
        comfort_par=comfort_parameters,
        write_set_map=write_set_map
    ) -> List[Dict]:
        pass

    @task(template=MergeFolderData, needs=[run_comfort_map])
    def restructure_temperature_results(
        self, input_folder='initial_results/results/temperature', extension='csv'
    ):
        return [
            {
                'from': MergeFolderData()._outputs.output_folder,
                'to': 'results/temperature'
            }
        ]

    @task(template=MergeFolderData, needs=[run_comfort_map])
    def restructure_condition_results(
        self, input_folder='initial_results/results/condition', extension='csv'
    ):
        return [
            {
                'from': MergeFolderData()._outputs.output_folder,
                'to': 'results/condition'
            }
        ]

    @task(template=MergeFolderData, needs=[run_comfort_map])
    def restructure_condition_intensity_results(
        self, input_folder='initial_results/results/condition_intensity', extension='csv'
    ):
        return [
            {
                'from': MergeFolderData()._outputs.output_folder,
                'to': 'results/condition_intensity'
            }
        ]

    @task(template=MergeFolderData, needs=[run_comfort_map])
    def restructure_tcp_results(
        self, input_folder='initial_results/metrics/TCP', extension='csv'
    ):
        return [
            {
                'from': MergeFolderData()._outputs.output_folder,
                'to': 'metrics/TCP'
            }
        ]

    @task(template=MergeFolderData, needs=[run_comfort_map])
    def restructure_hsp_results(
        self, input_folder='initial_results/metrics/HSP', extension='csv'
    ):
        return [
            {
                'from': MergeFolderData()._outputs.output_folder,
                'to': 'metrics/HSP'
            }
        ]

    @task(template=MergeFolderData, needs=[run_comfort_map])
    def restructure_csp_results(
        self, input_folder='initial_results/metrics/CSP', extension='csv'
    ):
        return [
            {
                'from': MergeFolderData()._outputs.output_folder,
                'to': 'metrics/CSP'
            }
        ]

    @task(template=MapResultInfo)
    def create_result_info(
        self, comfort_model='pmv', run_period=run_period, qualifier=write_set_map
    ) -> List[Dict]:
        return [
            {
                'from': MapResultInfo()._outputs.temperature_info,
                'to': 'results/temperature/results_info.json'
            },
            {
                'from': MapResultInfo()._outputs.condition_info,
                'to': 'results/condition/results_info.json'
            },
            {
                'from': MapResultInfo()._outputs.condition_intensity_info,
                'to': 'results/condition_intensity/results_info.json'
            }
        ]

    # outputs
    environmental_conditions = Outputs.folder(
        source='initial_results/conditions',
        description='A folder containing the environmental conditions that were input '
        'to the thermal comfort model. This include the MRT, air temperature, longwave '
        'MRT, shortwave MRT delta and relative humidity.', alias=env_conditions_output
    )

    temperature = Outputs.folder(
        source='results/temperature', description='A folder containing CSV maps of '
        'Operative Temperature for each sensor grid. Alternatively, if the '
        'write-set-map option is used, the CSV maps here will contain Standard '
        'Effective Temperature (SET). Values are in Celsius.',
        alias=operative_or_set_output
    )

    condition = Outputs.folder(
        source='results/condition', description='A folder containing CSV maps of '
        'comfort conditions for each sensor grid. -1 indicates unacceptably cold '
        'conditions. +1 indicates unacceptably hot conditions. 0 indicates neutral '
        '(comfortable) conditions.', alias=thermal_condition_output
    )

    pmv = Outputs.folder(
        source='results/condition_intensity', description='A folder containing CSV maps '
        'of the Predicted Mean Vote (PMV) for each sensor grid. This can be used '
        'to understand not just whether conditions are acceptable but how '
        'uncomfortably hot or cold they are.', alias=pmv_output
    )

    tcp = Outputs.folder(
        source='metrics/TCP', description='A folder containing CSV values for Thermal '
        'Comfort Percent (TCP). TCP is the percentage of occupied time where '
        'thermal conditions are acceptable/comfortable.', alias=tcp_output
    )

    hsp = Outputs.folder(
        source='metrics/HSP', description='A folder containing CSV values for Heat '
        'Sensation Percent (HSP). HSP is the percentage of occupied time where '
        'thermal conditions are hotter than what is considered acceptable/comfortable.',
        alias=hsp_output
    )

    csp = Outputs.folder(
        source='metrics/CSP', description='A folder containing CSV values for Cold '
        'Sensation Percent (CSP). CSP is the percentage of occupied time where '
        'thermal conditions are colder than what is considered acceptable/comfortable.',
        alias=csp_output
    )

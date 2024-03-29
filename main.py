import numpy as np
from typing import Tuple
from ui import MatPlotLibUI
from car import Car
from ooi import OOI
from vkf import VectorizedStaticKalmanFilter
from mcts.mcts import MCTS
from mcts.hash import hash_action, hash_state
from mcts.tree_viz import render_graph, render_pyvis
from flask_server import FlaskServer
import time

class ToyMeasurementControl:
    def __init__(self):
        # Determine update rate
        self.hz = 20.0
        self.period = 1.0 / self.hz
        
        # Parameters
        self.final_cov_trace = 0.1
        self.action_space_sample_heuristic = 'uniform_discrete'
        self.velocity_options = 5  # number of discrete options for velocity
        self.steering_angle_options = 5  # number of discrete options for steering angle
        self.horizon = 30 # length of the planning horizon
        self.random_iterations = 100  # number of random iterations for MCTS (limited by horizon anyways)
        self.learn_iterations = 5  # number of learning iterations for MCTS
        
        # Create a plotter object
        self.ui = MatPlotLibUI(update_rate=self.hz)
        
        # Create a car object
        self.car = Car(self.ui, np.array([50.0, 30.0]), 90, self.hz)
        
        # Create an OOI object
        self.ooi = OOI(self.ui, position=(50,50), car_max_range=self.car.max_range, car_max_bearing=self.car.max_bearing)
        
        # Create a Static Vectorized Kalman Filter object
        self.vkf = VectorizedStaticKalmanFilter(np.array([50]*8), np.diag([8]*8), 4.0)
        
        # Save the last action (mainly used for relative manual control)
        self.last_action = np.array([0.0, 0.0])
        
        # Run flask server which makes web MCTS tree display and communicates clicked nodes
        self.flask_server = FlaskServer()
        self.flask_server.run_thread()
        self.last_node_clicked = None
        self.last_node_hash_clicked = None
        
        # Flag for whether simulated state is being drawn
        self.drawing_simulated_state = False


    def run(self): 
    # Loop until matplotlib window is closed (handled by the UI class)
        while(True):
            
            # Only update controls if the UI is not paused
            if not self.ui.paused:
                
                # Get the observation from the OOI, pass it to the KF for update
                observable_corners, indeces = self.ooi.get_noisy_observation(self.car.get_state())
                self.vkf.update(observable_corners, indeces, self.car.get_state())
                
                ############################ AUTONOMOUS CONTROL ############################
                # Create an MCTS object
                # print("NEW MCTS ITERATION-------------------")
                # print()
                # print("Initial State: ", self.get_state())
                mcts = MCTS(initial_obs=self.get_state(), env=self, K=0.3**5,
                            _hash_action=hash_action, _hash_state=hash_state, 
                            random_iterations=self.random_iterations)
                mcts.learn(self.learn_iterations, progress_bar=False)
                action_vector = mcts.best_action()
                print("MCTS Action: ", action_vector)
                render_pyvis(mcts.root)
                
                
                ############################ MANUAL CONTROL ############################
                # Get the control inputs from the arrow keys, pass them to the car for update
                # relative_action_vector = self.car.get_arrow_key_control()
                # action_vector = self.car.add_input(relative_action_vector, self.last_action)   # This adds the control input rather than directly setting it (easier for keyboard control)
                
                # Update the car's state based on the control inputs
                self.car.update(self.period, action_vector)
                
                self.drawing_simulated_state = False
                
            else:
                # Check if a node has been clicked
                if self.last_node_hash_clicked != self.flask_server.node_clicked:
                    
                    clicked_node = mcts.get_node(self.flask_server.node_clicked)
                    
                    if clicked_node is None:
                        print("Node not found")
                        print("Either refresh (viewing old tree) or click on decision node (not random node)")
                    else:
                        self.drawing_simulated_state = True
                        self.last_node_clicked = clicked_node
                        self.last_node_hash_clicked = self.flask_server.node_clicked
                        print("Node clicked: \n", self.last_node_clicked)
            
            # Draw either simulated state or current state
            if self.drawing_simulated_state:
                # Update displays based on clicked node
                self.car.draw_state(self.last_node_clicked.state[0])
                self.vkf.draw_state(self.last_node_clicked.state[1], self.last_node_clicked.state[2], self.ui)
                self.ooi.draw() # Just draw rectangles for now
            else:
                # Update current state displays
                self.car.draw()
                self.ooi.draw()
                self.vkf.draw(self.ui)
            
            # Update the ui to display the new state
            self.ui.update()
            
            # Check for shutdown flag
            if self.ui.shutdown:
                self.flask_server.stop_flask()
                break
            
    def get_state(self, horizon=0) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int]:
        '''
        Returns full state -> Tuple[Car State, Corner Mean, Corner Covariance, Horizon]
        '''
        return self.car.get_state(), self.vkf.get_mean(), self.vkf.get_covariance(), horizon
    
    def step(self, state, action) -> Tuple[float, np.ndarray]:
        """
        Step the environment by one time step. The action is applied to the car, and the state is observed by the OOI.
        The observation is then passed to the KF for update.
        
        :param state: (np.ndarray) the state of the car and KF (Car state(x,y,yaw), corner means, corner covariances)
        :param action: (np.ndarray) the control input to the car (velocity, steering angle)
        :return: (float, np.ndarray) the reward of the state-action pair, and the new state
        """
        # print("Starting forward simulation state: ", state)
        
        # Pull out the state elements
        car_state, corner_means, corner_cov, horizon = state
        
        # Increment the horizon
        horizon += 1
        
        # Apply the action to the car
        new_car_state = self.car.update(self.period, action, simulate=True, starting_state=car_state)
        
        # Get the observation from the OOI, pass it to the KF for update
        observable_corners, indeces = self.ooi.get_observation(new_car_state, draw=False)
        new_mean, new_cov = self.vkf.update(observable_corners, indeces, new_car_state, 
                                            simulate=True, s_k_=corner_means, P_k_=corner_cov)
        
        # Combine the updated car state, mean, covariance and horizon into a new state
        new_state = (new_car_state, new_mean, new_cov, horizon)
        
        # Find the reward based on updated state
        reward, done = self.get_reward(new_state, action)
        
        # Check if we are at the end of the horizon
        if not done and horizon == self.horizon-1:
            done = True
        
        # Return the reward and the new state
        return new_state, reward, done
    
    def get_reward(self, state, action) -> Tuple[float, bool]:
        """
        Get the reward of the new state-action pair.
        
        :param new_state: (np.ndarray) the new state of the car and OOI (position(0:2), corner means(2:10), corner covariances(10:74))
        :param action: (np.ndarray) the control input to the car (velocity, steering angle)
        :return: (float, bool) the reward of the state-action pair, and whether the episode is done
        """
        # Pull out the state elements
        car_state, corner_means, corner_cov, horizon = state
        
        # Find the sum of the diagonals
        trace = np.trace(corner_cov)
        
        # Find whether the episode is done TODO: done needs to also account for horizon length
        done = trace < self.final_cov_trace

        # Find the reward
        reward = -trace
        
        return reward, done
    
    def action_space_sample(self) -> np.ndarray:
        """
        Sample an action from the action space.
        
        :return: (np.ndarray) the sampled action
        """
        # Uniform sampling in continuous space
        if self.action_space_sample_heuristic == 'uniform_continuous':
            velocity = np.random.uniform(0, self.car.max_velocity)
            steering_angle = np.random.uniform(-self.car.max_steering_angle, self.car.max_steering_angle)
            
        # Uniform Discrete sampling with a specified number of options
        if self.action_space_sample_heuristic == 'uniform_discrete':
            velocity = np.random.choice(np.linspace(0, self.car.max_velocity, self.velocity_options))
            steering_angle = np.random.choice(np.linspace(-self.car.max_steering_angle, self.car.max_steering_angle, self.steering_angle_options))
            
        return np.array([velocity, steering_angle])
    
    
if __name__ == '__main__':  
    tmc = ToyMeasurementControl()
    tmc.run()
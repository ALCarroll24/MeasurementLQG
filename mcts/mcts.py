'''
modified from
https://github.com/martinobdl/MCTS
'''

import cloudpickle
import numpy as np
# import gym
from .nodes import DecisionNode, RandomNode
from typing import Callable, List, Tuple, Any, Optional
from utils import wrap_angle, min_max_normalize


class MCTS:
    """
    Base class for MCTS based on Monte Carlo Tree Search for Continuous and Stochastic Sequential
    Decision Making Problems, Courtoux

    :param initial_obs: (int or tuple) initial state of the tree. Returned by env.reset().
    :param env: (gym env) game environment
    :param K: (float) exporation parameter of UCB
    """

    def __init__(
        self, 
        initial_obs, 
        env, 
        K: float,
        _hash_action: Callable[[Any], Tuple],
        _hash_state: Callable[[Any], Tuple],
        expansion_branch_factor: int = 2,
        deterministic: bool = True,
        alpha: float = 0.5,
        beta: float = 0.5,
        evaluation_multiplier: float = 1.0,
    ):
        
        self.env = env
        self.K = K
        self.root = DecisionNode(state=initial_obs, is_root=True)
        self._initialize_hash(_hash_action, _hash_state)
        self.expansion_branch_factor = expansion_branch_factor
        self.deterministic = deterministic
        self.alpha = alpha
        self.beta = beta
        self.evaluation_multiplier = evaluation_multiplier

    def get_node(self, node_hash: int) -> Optional[DecisionNode]:
        """
        Returns the node from the tree given its hash

        :param node_hash: (int) hash of the node to retrieve
        :return: (DecisionNode) the node corresponding to the hash
        """
        def _get_node(node: DecisionNode, node_hash: int) -> Optional[DecisionNode]:
            if node.__hash__() == node_hash:
                return node
            for _, random_node in node.children.items():
                for _, next_decision_node in random_node.children.items():
                    found_node = _get_node(next_decision_node, node_hash)
                    if found_node is not None:
                        return found_node
            return None

        return _get_node(self.root, node_hash)

    def _initialize_hash(
        self, 
        _hash_action: Callable[[Any], Tuple],
        _hash_state: Callable[[Any], Tuple],
    ):
        """
        Set the hash preprocessors of the state and the action, 
        in order to make them hashable.

        Need to be customized based on the definition of state and action
        """

        self._hash_action = _hash_action
        self._hash_state = _hash_state

    def _collect_data(self, action_vector: Any = None):
        """
        Collects the data and parameters to save.
        """
        data = {
            "K": self.K,
            "nodes": [],
            "decision": action_vector,
        }

        self._traverse_tree(self.root, data)
        # do a dfs for all nodes

        return data
    
    def _traverse_tree(self, node: DecisionNode, storage_dict: dict):

        if node.is_final:   return

        position, _, _ = node.state

        for _, random_node in node.children.items():
            for _, next_decision_node in random_node.children.items():
                next_position, _, time_step = next_decision_node.state
                node_dict = {"position": next_position,
                        "parent_position": position,
                        "time_step": time_step,
                        "visits": next_decision_node.visits,
                        }
                storage_dict["nodes"].append(node_dict)
                self._traverse_tree(next_decision_node, storage_dict=storage_dict)


    def update_decision_node(
        self, 
        decision_node: DecisionNode, 
        random_node: RandomNode, 
        hash_preprocess: Callable,
    ):
        """
        Return the decision node of drawn by the select outcome function.
        If it's a new node, it gets appended to the random node.
        Else returns the decsion node already stored in the random node.

        :param decision_node (DecisionNode): the decision node to update
        :param random_node (RandomNode): the random node of which the decison node is a child
        :param hash_preprocess (fun: gym.state -> hashable) function that sepcifies the preprocessing in order to make
        the state hashable.
        """

        if hash_preprocess(decision_node.state) not in random_node.children.keys():
            decision_node.parent = random_node
            random_node.add_children(decision_node, hash_preprocess)
        else:
            decision_node = random_node.children[hash_preprocess(decision_node.state)]

        return decision_node

    def grow_tree(self):
        """
        Explores the current tree with the UCB principle until we reach an unvisited node
        where the reward is obtained with random rollouts.
        """

        decision_node = self.root
        internal_env = self.env

        ## SELECTION PHASE
        # While goal has not been reached and we are not at a leaf node (no children)
        while (not decision_node.is_final) and len(decision_node.children) > 0:
            # print("Starting Decision node: ", decision_node)
            
            # Get action from this decision node using UCB
            a = self.select(decision_node)
            # print(f"Action selected: {a}")

            # Create new random node or get the existing one from the action
            new_random_node = decision_node.next_random_node(a, self._hash_action)
            # print(f"New Random node: {new_random_node}")

            # Create new decision node using environment step function, if stochastic, use environment to get the next state and reward
            if not self.deterministic:
                (new_decision_node, r) = self.select_outcome(internal_env, new_random_node)
            
            # If deterministic, we have already simulated this node, so just get the child decision node
            else:
                new_decision_node = list(new_random_node.children.values())[0]
                r = new_decision_node.reward
            
            # print(f"New Decision node: {new_decision_node}")

            # Ensure that the decision node is connected to its parent random node (not sure why it wouldn't be though...?)
            new_decision_node = self.update_decision_node(new_decision_node, new_random_node, self._hash_state)
            # print(f"Updated Decision node: {new_decision_node}")

            # Set the reward of the new nodes
            new_decision_node.reward = r
            new_random_node.reward = r

            # Continue the tree traversal
            decision_node = new_decision_node


        ### EXPANSION PHASE
        # If have already evaluated this node (visited more than once), Add a new node to the tree and evaluate it instead
        if decision_node.visits == 0 and not decision_node.is_final:
            # If we are expanding with all actions
            if self.expansion_branch_factor == -1:
                for a in self.env.all_actions:
                    # Action -> random node -> environment step -> decision node
                    self.expand(decision_node, a)
            
            # Otherwise if we are sampling a number of times from the action space
            else:
                for i in range(self.expansion_branch_factor):
                    # Random action -> random node -> environment step -> decision node
                    a = self.env.action_space_sample()
                    self.expand(decision_node, a)

        # Add a visit since we ended traversal on this decision node
        decision_node.visits += 1
        
        
        ### SIMULATION PHASE
        # Currently utilizing random actions to evaluate reward (general evaluation, the monte carlo part)
        cumulative_reward = self.evaluate(decision_node.state)
        decision_node.evaluation_reward = cumulative_reward
        
        
        ### BACKPROPAGATION PHASE
        # Back propagate the reward back to the root
        while not decision_node.is_root:
            random_node = decision_node.parent
            cumulative_reward += random_node.reward
            random_node.cumulative_reward += cumulative_reward
            random_node.visits += 1
            decision_node = random_node.parent
            decision_node.visits += 1

    def expand(self, decision_node: DecisionNode, action: np.ndarray):
        # Random action -> random node -> environment step -> decision node
        new_random_node = decision_node.next_random_node(action, self._hash_action)
        (new_decision_node, r) = self.select_outcome(self.env, new_random_node)
        new_decision_node = self.update_decision_node(new_decision_node, new_random_node, self._hash_state)
        new_decision_node.reward = r
        new_random_node.reward = r

    def evaluate(self, state) -> float:
        """
        Evaluates a DecisionNode by using a convex combination of distance to closest OOI point and angle to OOI.
        
        :param env: (gym.env) gym environemt that describes the state at the node to evaulate.
        :return: (float) the cumulative reward observed during the tree traversing.
        """
        # Pull out the car position and yaw from the state
        car_state = state[0]
        car_pos = car_state[0:2]
        car_yaw = car_state[2]
        
        # Pull out the OOI corner positions from the state
        ooi_state = state[1]
        ooi_reshaped = np.reshape(ooi_state, (4, 2))
        
        # Pull out covariances of corners and get trace for each corner
        cov_state = state[2]
        cov_diag = np.diag(cov_state)
        corner_traces = np.zeros((int(cov_diag.shape[0]/2),))
        
        # Iterate through covariance diagonal elements
        for i in range(corner_traces.shape[0]):
            # Calculate trace of covariance matrix for each corner
            corner_traces[i] = cov_diag[i*2] + cov_diag[(i*2) + 1]
        
        # Calculate squared distance of Car to OOI points
        squared_dists = np.sum((ooi_reshaped - car_pos)**2, axis=1)
        
        # Create scaled cumulative distance and bearing outputs
        cum_dist = 0.
        cum_bearing = 0.
        
        # Iterate through each corner trace
        for i in range(corner_traces.shape[0]):
            # Find bearing to current corner
            ooi_bearing = np.arctan2(ooi_reshaped[i, 1] - car_pos[1], ooi_reshaped[i, 0] - car_pos[0])
            
            # Subtract the car yaw to get the relative bearing to the OOI point
            bearing_delta = abs(ooi_bearing - car_yaw)
            
            # Normalize both the distance and bearing to be between 0 and 1
            norm_dist = min_max_normalize(squared_dists[i], 0., 2000.)
            norm_bearing = min_max_normalize(bearing_delta, -np.pi, np.pi)
            
            # Add covariance trace weighted bearing and squared distance to cumulative distance and bearing
            cum_dist += corner_traces[i] * norm_dist
            cum_bearing += corner_traces[i] * norm_bearing
        
        # Return weighted convex combination (alpha and beta add to 1) of cumulative covariance weighted distance and bearing
        # This is negative because we want to minimize the distance and bearing (punishment for being far away or off bearing)
        return -self.evaluation_multiplier * (self.alpha * cum_dist + self.beta * cum_bearing)


    # Random action evaluation, doesn't really make sense for this problem
    # def evaluate(self, env, state) -> float:
    #     """
    #     a customized function, don't have to be

    #     Evaluates a DecionNode playing until an terminal node using the rollotPolicy,
        

    #     :param env: (gym.env) gym environemt that describes the state at the node to evaulate.
    #     :return: (float) the cumulative reward observed during the tree traversing.
    #     """
    #     R = 0
    #     done = False
    #     iter = 0
    #     while ((not done) and (iter < self.random_iterations)):
    #         iter += 1
    #         a = env.action_space_sample()
    #         s, r, done = env.step(state, a)
    #         R += r

    #     return R

    def select_outcome(
        self, 
        env, 
        random_node: RandomNode,
    ) -> DecisionNode:
        """
        Given a RandomNode returns a DecisionNode

        :param: env: (gym env) the env that describes the state in which to select the outcome
        :param: random_node: (RandomNode) the random node from which selects the next state.
        :return: (DecisionNode) the selected Decision Node
        """
        new_state, r, done = env.step(random_node.parent.state, random_node.action)
        return DecisionNode(state=new_state, parent=random_node, is_final=done), r

    # def select(
    #     self, 
    #     x: DecisionNode,
    # ) -> Any:
    #     """
    #     Selects the action to play from the current decision node

    #     :param x: (DecisionNode) current decision node
    #     :return: action to play
    #     """
    #     scoring = False
    #     # If there are no random node children (actions) of this decision node
    #     if len(x.children) == 0:            
    #         # Create all of the random nodes for the actions and create the next decision node (state) to get reward
    #         for a in self.env.get_all_actions():
    #             # Use environment to get the next state and reward
    #             new_state, r, done = self.env.step(x.state, a)
                
    #             # Add the random node (action) to the decision node
    #             x.add_children(RandomNode(a, parent=x), hash_preprocess=self._hash_action)
                
    #             # Add the decision node (state) as a child of the random node we just made
    #             new_decision_node = DecisionNode(state=new_state, parent=x.children[self._hash_action(a)], is_final=done)
    #             x.children[self._hash_action(a)].add_children(new_decision_node, hash_preprocess=self._hash_state)

    #     def scoring(k):
    #         if x.children[k].visits > 0:
    #             print("In scoring function")
    #             scoring = True
    #             return x.children[k].cumulative_reward/x.children[k].visits + \
    #                 self.K*np.sqrt(np.log(x.visits)/x.children[k].visits)
    #         else:
    #             return np.inf

    #     a = max(x.children, key=scoring)
    #     if scoring:
    #         print("Action selected: ", a)
    #     return a
    
    def select(
        self, 
        x: DecisionNode,
    ) -> Any:
        """
        Selects the action to play from the current decision node

        :param x: (DecisionNode) current decision node
        :return: action to play
        """
        def scoring(k):
            if x.children[k].visits > 0:
                return x.children[k].cumulative_reward/x.children[k].visits + \
                    self.K*np.sqrt(np.log(x.visits)/x.children[k].visits)
            else:
                return np.inf

        a = max(x.children, key=scoring)

        return a

    def best_action(self):
        """
        At the end of the simulations returns the highest mean reward action
        
        : return: (tuple) the best action according to the mean reward
        """
        
        # Create a list of the mean rewards of the children
        children_key = list(self.root.children.keys())
        children_values = list(self.root.children.values())
        
        # Initialize mean reward list
        children_mean_rew = [0.0] * len(children_key)
        
        # Calculate the mean reward for each child
        for i in range(len(children_key)):
            children_mean_rew[i] = children_values[i].cumulative_reward / children_values[i].visits
            
        # Get the index of the highest mean reward
        index_best_action = np.argmax(children_mean_rew)
        
        # Return the action corresponding to the highest mean reward
        a = children_key[index_best_action]
        
        return a

    # ############################ Open Source Version ############################
    # def best_action(self):
    #     """
    #     At the end of the simulations returns the most visited action

    #     :return: (float) the best action according to the number of visits
    #     """

    #     number_of_visits_children = [node.visits for node in self.root.children.values()]
    #     index_best_action = np.argmax(number_of_visits_children)

    #     a = list(self.root.children.values())[index_best_action].action
    #     return a

    ############################ Tianqi's Version ############################
    # def best_action(self) -> Any:
    #     """
    #     At the end of the simulations returns the most visited action

    #     :return: (float) the best action according to the number of visits
    #     """

    #     action_vector = list()

    #     decision_node = self.root
    #     # depth = 0
    #     while not decision_node.is_final:
    #         # depth += 1
    #         number_of_visits_children = [node.visits for node in decision_node.children.values()]
    #         # avg_reward_children = [node.cumulative_reward/node.visits for node in decision_node.children.values()]
    #         # print(f'layer {depth}: {number_of_visits_children}')
    #         indices_most_visit = np.argwhere(number_of_visits_children == np.amax(number_of_visits_children)).flatten().tolist()
    #         # this may contain more than 1 children
    #         if len(indices_most_visit) == 1:
    #             index_best_action = indices_most_visit[0]
    #         else:
    #             avg_reward_list = []
    #             for index in indices_most_visit:
    #                 node = list(decision_node.children.values())[index]
    #                 element = (index, node.cumulative_reward/node.visits)
    #                 avg_reward_list.append(element)
    #             index_best_action = max(avg_reward_list, key = lambda x: x[1])[0]

    #         # index_best_action = np.argmax(number_of_visits_children)
    #         random_node = list(decision_node.children.values())[index_best_action]
    #         a = random_node.action
    #         action_vector.append(a)
    #         # find next decision state, only for determinisitic case
    #         # TODO need to consider the stochastic case
    #         assert len(random_node.children) == 1, print(random_node.children)
    #         decision_node = list(random_node.children.values())[0]

    #     # print("action output is {}".format(a))
    #     return action_vector

    def learn(
        self, 
        Nsim: int, 
        progress_bar=False,
    ):
        """
        Expand the tree and return the best action

        :param: Nsim: (int) number of tree traversals to do
        :param: progress_bar: (bool) wether to show a progress bar (tqdm)
        """

        if progress_bar:
            # iterations = tqdm(range(Nsim))
            iterations = range(Nsim)
        else:
            iterations = range(Nsim)
        for _ in iterations:
            
            # print("Next Learning Iteration")
            # print("Node hash: ", self.root.__hash__())
            # print("Root node: ", self.root)
            self.grow_tree()

    # TODO: visualize the MCTS process
    def save(self, path=None, action_vector: Any = None):
        """
        Saves the tree structure as a pkl.

        :param path: (str) path in which to save the tree
        """
        data = self._collect_data(action_vector=action_vector)

        # name = np.random.choice(['a', 'b', 'c', 'd', 'e', 'f']+list(map(str, range(0, 10))), size=8)
        # if path is None:
        #     path = './logs/'+"".join(name)+'_'
        # if os.path.exists(path):
        with open(path, "wb") as f:
            cloudpickle.dump(data, f)
        # print("Saved at {}".format(path))


if __name__ == "__main__":
    pass

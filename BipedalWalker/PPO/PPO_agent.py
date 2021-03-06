import random
import numpy as np
import gym
from collections import namedtuple
from collections import deque
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions import Normal
from torch.utils.data.sampler import BatchSampler, SubsetRandomSampler

from BipedalWalker.PPO.PPO_model import ActorNet,CriticNet

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

GAMMA=0.99
LR_a=0.001
LR_c=0.003
BATCH_SIZE=3
CLIP=0.2
UPDATE_TIME=10
max_grad_norm=0.5


class Memory():
    def __init__(self):
        self.trajectory=[]
        self.Transition = namedtuple('Transition', ['state', 'action', 'prob', 'reward'])

    def add(self,state,action,prob,reward):
        # state = torch.from_numpy(state).float().unsqueeze(0).to(device)
        self.trajectory.append(self.Transition(state,action,prob,reward))

    def clean_buffer(self):
        del self.trajectory[:]

    def __len__(self):
        return len(self.trajectory)


class PPO():

    def __init__(self, state_size, action_size):
        self.memory=Memory()

        self.policy = ActorNet(state_size, action_size).to(device)
        self.critic = CriticNet(state_size).to(device)

        self.policy_optimizer = optim.Adam(self.policy.parameters(), lr=LR_a)
        self.critic_optimizer = optim.Adam(self.critic.parameters(), lr=LR_c)

    def act(self,state):
        state = torch.from_numpy(state).float().unsqueeze(0).to(device)
        with torch.no_grad():
            (mu, sigma) = self.policy(state)  # 2d tensors
        dist = Normal(mu, sigma)
        action = dist.sample()
        action_log_prob = dist.log_prob(action)
        action = action.clamp(-2.0, 2.0)

        return action.numpy()[0], action_log_prob.numpy()[0]

    def update_network(self,exps):
        states, actions, old_probs, f_Rewrds = exps

        # -- update policy(actor) network -- #
        # get action probs from new policy
        (mus, sigmas) = self.policy(states)
        dists = Normal(mus, sigmas)
        new_probs=dists.log_prob(actions)
        ratios = torch.exp(new_probs - old_probs)

        # calculate advance from critic network
        V = self.critic(states)
        advantage = (f_Rewrds - V).detach()

        # calculate clipped surrogate function
        surr1 = ratios * advantage
        surr2 = torch.clamp(ratios, 1 - CLIP, 1 + CLIP) * advantage
        policy_loss = -torch.min(surr1, surr2).mean()

        # update parameters
        self.policy_optimizer.zero_grad()
        policy_loss.backward()
        nn.utils.clip_grad_norm_(self.policy.parameters(), max_grad_norm)
        self.policy_optimizer.step()

        # -- update value(critic) network -- #
        value_loss = F.mse_loss(f_Rewrds, V)
        self.critic_optimizer.zero_grad()
        value_loss.backward()
        nn.utils.clip_grad_norm_(self.critic.parameters(), max_grad_norm)
        self.critic_optimizer.step()


    def learn(self):
        """
        agent learn after finishing every episode.
        learn from experiences of this trajectory
        :return:
        """
        states = torch.tensor([t.state for t in self.memory.trajectory], dtype=torch.float)
        actions=torch.tensor([t.action for t in self.memory.trajectory],dtype=torch.float)
        old_probs=torch.tensor([t.prob for t in self.memory.trajectory],dtype=torch.float)

        # -- calculate discount future rewards for every time step
        rewards = [t.reward for t in self.memory.trajectory]
        fur_Rewards = []
        for i in range(len(rewards)):
            discount = [GAMMA ** i for i in range(len(rewards) - i)]
            f_rewards = rewards[i:]
            fur_Rewards.append(sum(d * f for d, f in zip(discount, f_rewards)))
        fur_Rewards=torch.tensor(fur_Rewards,dtype=torch.float).view(-1,1)

        for i in range(UPDATE_TIME):
            # -- repeat the flowing update loop for several times
            # disorganize transitions in the trajectory into sub groups
            for index_set in BatchSampler(SubsetRandomSampler(range(len(self.memory.trajectory))), BATCH_SIZE, False):
                exps=(states[index_set],actions[index_set],old_probs[index_set],fur_Rewards[index_set])
                # -- update policy network for every sub groups
                self.update_network(exps)

        self.memory.clean_buffer()  # clear trajectory


    def train(self,env):
        state = env.reset()
        total_reward=0
        while True:
            action,log_prob = self.act(state)
            next_state, reward, done, _ = env.step(action)
            # --store transition in this current trajectory
            self.memory.add(state,action,log_prob,reward)
            state=next_state
            total_reward+=reward
            if done:
                break

        if BATCH_SIZE <= len(self.memory):
            self.learn()

        return total_reward


if __name__=="__main__":
    env = gym.make('BipedalWalker-v2')
    agent = PPO(state_size=env.observation_space.shape[0], action_size=env.action_space.shape[0])
    n_episode = 1

    scores_deque = deque(maxlen=100)
    scores = []
    for i_episode in range(1, n_episode + 1):
        Reward = agent.train(env)


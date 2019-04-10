import multiprocessing as mp
from collections import deque

import numpy as np
import torch
from tensorboardX import SummaryWriter
from tqdm import tqdm

from environment import MultiprocessEnvironment
from experience import ExperienceStorage
from policy import RecurrentPolicy
from ppo import PPOAgent


MAX_X = 3161


def learn(num_envs: int,
          device: torch.device,  # CUDA or CPU
          total_steps: int = 512 * 8 * 2048,
          steps_per_update: int = 512,
          hidden_layer_size: int = 512,
          recurrent_hidden_size: int = 256,
          discount=0.98,
          gae_lambda=0.95,
          save_interval=128):
    envs = MultiprocessEnvironment(num_envs=num_envs)
    actor_critic = RecurrentPolicy(state_frame_channels=envs.observation_shape[0],
                                   action_space_size=envs.action_space_size,
                                   hidden_layer_size=hidden_layer_size,
                                   prev_actions_out_size=128,
                                   recurrent_hidden_size=recurrent_hidden_size,
                                   device=device)
    experience_storage = ExperienceStorage(num_steps=steps_per_update,
                                           num_envs=num_envs,
                                           observation_shape=envs.observation_shape,
                                           recurrent_hidden_size=recurrent_hidden_size,
                                           device=device)

    initial_observations = envs.reset()
    experience_storage.insert_initial_observations(initial_observations)

    episode_rewards = deque(maxlen=16)
    tb_writer = SummaryWriter()

    num_updates = total_steps // (num_envs * steps_per_update)
    agent = PPOAgent(actor_critic,
                     lr_lambda=lambda step: 1 - (step / float(num_updates)))

    for update_step in tqdm(range(num_updates)):
        for step in range(steps_per_update):
            with torch.no_grad():
                actor_input = experience_storage.get_actor_input(step)
                (values,
                 actions,
                 action_log_probs,
                 _,  # Action disribution entropy is not needed.
                 recurrent_hidden_states) = actor_critic.act(*actor_input)

            observations, rewards, done_values, info_dicts = envs.step(actions)
            masks = 1 - done_values
            experience_storage.insert(observations,
                                      actions,
                                      action_log_probs,
                                      rewards,
                                      values,
                                      masks,
                                      recurrent_hidden_states)

            for done, info in zip(done_values, info_dicts):
                if done:
                    level_completed_percentage = info['x_pos'] / MAX_X
                    episode_rewards.append(level_completed_percentage)

        with torch.no_grad():
            critic_input = experience_storage.get_critic_input()
            next_value = actor_critic.value(*critic_input)

        experience_storage.compute_returns(next_value,
                                           discount=discount,
                                           gae_lambda=gae_lambda)

        losses = agent.update(experience_storage)

        if episode_rewards:
            with torch.no_grad():
                cumulative_reward = experience_storage.rewards.sum((0, 2))
                mean_reward = cumulative_reward.mean()
                std_reward = cumulative_reward.std()

            tb_writer.add_scalar('mario/lr', agent.current_lr(), update_step)
            tb_writer.add_scalars('mario/level_progress', {
                'min': np.min(episode_rewards),
                'max': np.max(episode_rewards),
                'mean': np.mean(episode_rewards),
                'median': np.median(episode_rewards),
            }, update_step)

            tb_writer.add_scalars('mario/reward', {'mean': mean_reward,
                                                   'std': std_reward}, update_step)
            tb_writer.add_scalars('mario/loss', {
                'policy': losses['policy_loss'],
                'value': losses['value_loss'],
                'action_dist_entropy': losses['action_dist_entropy']
            }, update_step)

        save_model = (update_step % save_interval) == (save_interval - 1)
        if save_model:
            model_path = 'models/model_{}.bin'.format(update_step + 1)
            torch.save(actor_critic.state_dict(), model_path)

    tb_writer.close()


if __name__ == '__main__':
    cpu_count = mp.cpu_count()
    device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    learn(num_envs=cpu_count, device=device)
